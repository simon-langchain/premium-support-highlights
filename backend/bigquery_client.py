"""BigQuery client for QBR chart data and maturity scorecard.

Queries langchain-prod.dbt.* tables using a dedicated service account.

Required env var (optional — falls back to ADC):
  BIGQUERY_SERVICE_ACCOUNT_JSON  — service account with BigQuery Data Viewer on langchain-prod.dbt

Key functions:
  fetch_chart_data(metronome_id)   — 7 queries for QBR chart slides (usage, evals, contract, etc.)
  fetch_maturity_data(metronome_id) — maturity scorecard with stage scores per dimension
"""

import json
import logging
import os

_log = logging.getLogger(__name__)

_PROJECT = "langchain-prod"
_DATASET = "dbt"


def _client():
    from google.cloud import bigquery

    # Prefer a dedicated BQ service account (langchain-prod access required).
    # Falls back to Application Default Credentials (gcloud auth application-default
    # login locally; Workload Identity / attached SA in production).
    raw = (
        os.environ.get("BIGQUERY_SERVICE_ACCOUNT_JSON")
        or os.environ.get("GOOGLE_BIGQUERY_SERVICE_ACCOUNT_JSON")
        or ""
    ).strip()
    if raw:
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(
            json.loads(raw),
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        return bigquery.Client(project=_PROJECT, credentials=creds)

    # ADC — works with `gcloud auth application-default login` locally,
    # or with a service account attached to the LSD deployment.
    # Override the quota/billing project to langchain-prod so ADC doesn't
    # use whatever stale project is in the local gcloud config.
    import google.auth
    creds, _ = google.auth.default()
    if hasattr(creds, "with_quota_project"):
        creds = creds.with_quota_project(_PROJECT)
    return bigquery.Client(project=_PROJECT, credentials=creds)


def _tbl(name: str) -> str:
    return f"`{_PROJECT}.{_DATASET}.{name}`"


def fetch_chart_data(metronome_id: str) -> dict | None:
    """Return chart data dict for the given Metronome customer ID.

    Runs 7 BigQuery queries. Returns None if client init fails entirely;
    returns a partial dict if individual queries fail (empty list/dict per key).

    Keys returned:
      "monthly_usage"     — monthly trace/agent run counts (last 12 months)
      "cumulative_usage"  — daily running totals within active contract
      "page_views"        — monthly page view counts (last 12 months)
      "evaluator_usage"   — monthly evaluator rules by category (last 12 months)
      "contract_metrics"  — single-row dict: contract_end_date, pct_into_contract, pct_commit_used
      "enablement_stats"  — single-row dict: academy_enrolled, billable_seats, est_engineering_headcount
      "sign_ups_by_course" — list of {course_name, sign_ups} dicts
    """
    if not metronome_id:
        return None

    try:
        client = _client()
    except Exception as exc:
        _log.warning("BigQuery client init failed (fetch_chart_data): %s", exc)
        return None

    from google.cloud import bigquery
    import datetime as _dt

    def _param(name: str, value: str) -> bigquery.QueryJobConfig:
        return bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(name, "STRING", value)
            ]
        )

    def _rows_to_dicts(rows) -> list[dict]:
        """Convert BQ Row objects to plain dicts, serializing date/datetime to ISO strings."""
        result = []
        for row in rows:
            d = {}
            for key, val in row.items():
                if isinstance(val, (_dt.date, _dt.datetime)):
                    d[key] = val.isoformat()
                else:
                    d[key] = val
            result.append(d)
        return result

    chart_data: dict = {}

    # ------------------------------------------------------------------
    # 1. Monthly usage — active contract period
    # For SH customers, experiments/prompt_commits/prompt_pulls in
    # fct__organization_usage_daily are 0; overlay the latest month with
    # values from stg_postgres__usage_snapshots (same logic as Hex).
    # ------------------------------------------------------------------
    try:
        q = f"""
            WITH sh_customer AS (
                SELECT self_hosted_customer_id
                FROM {_tbl("dim__self_hosted_licenses")}
                WHERE metronome_customer_id = @metronome_id
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY metronome_customer_id
                    ORDER BY is_license_verified_last_day DESC,
                             is_license_verified_last_7_days DESC,
                             last_verified_at_utc DESC
                ) = 1
            ),
            latest_rx_sh AS (
                SELECT MAX(received_at_utc) AS max_rx
                FROM {_tbl("stg_postgres__usage_snapshots")}
                WHERE self_hosted_customer_id IN (SELECT self_hosted_customer_id FROM sh_customer)
            ),
            sh_snap AS (
                SELECT
                    SUM(experiments)   AS experiments,
                    SUM(prompt_commits) AS prompt_commits,
                    SUM(prompt_pulls)  AS prompt_pulls,
                    SUM(datasets)      AS datasets
                FROM {_tbl("stg_postgres__usage_snapshots")} AS s
                CROSS JOIN latest_rx_sh AS lr
                WHERE s.self_hosted_customer_id IN (SELECT self_hosted_customer_id FROM sh_customer)
                  AND s.received_at_utc BETWEEN DATETIME_SUB(lr.max_rx, INTERVAL 1 HOUR)
                                            AND DATETIME_ADD(lr.max_rx, INTERVAL 1 HOUR)
            ),
            contract_start AS (
                SELECT MIN(CAST(contract_start_at_utc AS DATE)) AS start_date
                FROM {_tbl("dim__contracts")}
                WHERE metronome_customer_id = @metronome_id
                  AND is_active_contract = TRUE
            ),
            monthly_base AS (
                SELECT
                  DATE_TRUNC(date_day, MONTH)            AS month_start,
                  SUM(billable_trace_count)              AS billable_traces,
                  SUM(billable_nodes_executed)           AS billable_nodes_executed,
                  SUM(billable_agent_runs)               AS billable_agent_runs,
                  SUM(billable_agent_builder_runs_count) AS billable_agent_builder_runs,
                  SUM(actual_trace_count_sent)           AS actual_traces,
                  SUM(actual_agent_runs)                 AS actual_agent_runs,
                  SUM(actual_agent_builder_runs_count)   AS actual_agent_builder_runs,
                  SUM(total_experiments)                 AS raw_experiments,
                  SUM(total_playground_prompt_commits)   AS raw_prompt_commits,
                  SUM(total_playground_prompt_pulls)     AS raw_prompt_pulls,
                  SUM(total_datasets)                    AS total_datasets
                FROM {_tbl("fct__organization_usage_daily")}
                CROSS JOIN contract_start
                WHERE organization_id IN (
                    SELECT organization_id FROM {_tbl("dim__organizations")}
                    WHERE metronome_customer_id = @metronome_id
                )
                AND date_day >= contract_start.start_date
                GROUP BY 1
            ),
            latest_month AS (
                SELECT MAX(month_start) AS max_month FROM monthly_base
            )
            SELECT
              m.month_start,
              m.billable_traces,
              m.billable_nodes_executed,
              m.billable_agent_runs,
              m.billable_agent_builder_runs,
              m.actual_traces,
              m.actual_agent_runs,
              m.actual_agent_builder_runs,
              -- SH overlay: for the latest month, replace with snapshot values when SH
              CASE
                  WHEN m.month_start = (SELECT max_month FROM latest_month)
                       AND (SELECT COUNT(*) FROM sh_snap) > 0
                  THEN COALESCE((SELECT experiments FROM sh_snap), m.raw_experiments, 0)
                  ELSE m.raw_experiments
              END AS total_experiments,
              CASE
                  WHEN m.month_start = (SELECT max_month FROM latest_month)
                       AND (SELECT COUNT(*) FROM sh_snap) > 0
                  THEN COALESCE((SELECT prompt_commits FROM sh_snap), m.raw_prompt_commits, 0)
                  ELSE m.raw_prompt_commits
              END AS total_prompt_commits,
              CASE
                  WHEN m.month_start = (SELECT max_month FROM latest_month)
                       AND (SELECT COUNT(*) FROM sh_snap) > 0
                  THEN COALESCE((SELECT prompt_pulls FROM sh_snap), m.raw_prompt_pulls, 0)
                  ELSE m.raw_prompt_pulls
              END AS total_prompt_pulls,
              CASE
                  WHEN m.month_start = (SELECT max_month FROM latest_month)
                       AND (SELECT COUNT(*) FROM sh_snap) > 0
                  THEN COALESCE((SELECT datasets FROM sh_snap), m.total_datasets, 0)
                  ELSE m.total_datasets
              END AS total_datasets
            FROM monthly_base AS m
            ORDER BY m.month_start
        """
        job = client.query(q, job_config=_param("metronome_id", metronome_id))
        chart_data["monthly_usage"] = _rows_to_dicts(job.result())
        _log.info("fetch_chart_data: monthly_usage rows=%d", len(chart_data["monthly_usage"]))
    except Exception as exc:
        _log.warning("fetch_chart_data: monthly_usage query failed: %s", exc)
        chart_data["monthly_usage"] = []

    # ------------------------------------------------------------------
    # 2. Cumulative usage — running totals within active contract
    # ------------------------------------------------------------------
    try:
        q = f"""
            WITH contract AS (
                SELECT
                  MIN(CAST(contract_start_at_utc AS DATE)) AS start_date,
                  MAX(CAST(contract_end_at_utc AS DATE)) AS end_date
                FROM {_tbl("dim__contracts")}
                WHERE metronome_customer_id = @metronome_id
                  AND is_active_contract = TRUE
            ),
            daily AS (
                SELECT
                  f.date_day AS usage_date,
                  SUM(f.billable_trace_count) AS traces,
                  SUM(f.billable_nodes_executed) AS nodes,
                  SUM(f.billable_agent_runs) AS agent_runs,
                  SUM(f.billable_longlived_trace_count) AS longlived_traces,
                  SUM(f.billable_agent_builder_runs_count) AS ab_runs
                FROM {_tbl("fct__organization_usage_daily")} f
                CROSS JOIN contract c
                WHERE f.metronome_customer_id = @metronome_id
                  AND f.date_day >= c.start_date
                  AND f.date_day <= LEAST(c.end_date, CURRENT_DATE())
                GROUP BY 1
            )
            SELECT
              usage_date,
              SUM(traces) OVER (ORDER BY usage_date) AS running_total_traces,
              SUM(nodes) OVER (ORDER BY usage_date) AS running_total_nodes,
              SUM(agent_runs) OVER (ORDER BY usage_date) AS running_total_agent_runs,
              SUM(longlived_traces) OVER (ORDER BY usage_date) AS running_billable_longlived_trace_count,
              SUM(ab_runs) OVER (ORDER BY usage_date) AS running_billable_agent_builder_runs
            FROM daily
            ORDER BY usage_date
        """
        job = client.query(q, job_config=_param("metronome_id", metronome_id))
        chart_data["cumulative_usage"] = _rows_to_dicts(job.result())
        _log.info("fetch_chart_data: cumulative_usage rows=%d", len(chart_data["cumulative_usage"]))
    except Exception as exc:
        _log.warning("fetch_chart_data: cumulative_usage query failed: %s", exc)
        chart_data["cumulative_usage"] = []

    # ------------------------------------------------------------------
    # 3. Page views — active contract period (joined through dim__organizations)
    # ------------------------------------------------------------------
    try:
        q = f"""
            WITH contract_start AS (
                SELECT MIN(CAST(contract_start_at_utc AS DATE)) AS start_date
                FROM {_tbl("dim__contracts")}
                WHERE metronome_customer_id = @metronome_id
                  AND is_active_contract = TRUE
            )
            SELECT
              DATE_TRUNC(DATE(event_at_utc), MONTH) AS event_month,
              COUNT(*) AS total_page_views
            FROM {_tbl("fct__page_views")}
            CROSS JOIN contract_start
            WHERE organization_id IN (
                SELECT organization_id FROM {_tbl("dim__organizations")}
                WHERE metronome_customer_id = @metronome_id
            )
            AND event_at_utc >= DATETIME(contract_start.start_date)
            GROUP BY 1
            ORDER BY 1
        """
        job = client.query(q, job_config=_param("metronome_id", metronome_id))
        chart_data["page_views"] = _rows_to_dicts(job.result())
        _log.info("fetch_chart_data: page_views rows=%d", len(chart_data["page_views"]))
    except Exception as exc:
        _log.warning("fetch_chart_data: page_views query failed: %s", exc)
        chart_data["page_views"] = []

    # ------------------------------------------------------------------
    # 4. Evaluator usage — active contract period, monthly by category.
    #    SH fallback: fct__evaluator_usage_daily has no rows for self-hosted
    #    customers; use stg_postgres__usage_snapshots.run_rules as a proxy
    #    when the direct query returns nothing.
    # ------------------------------------------------------------------
    try:
        q = f"""
            WITH sh_customer AS (
                SELECT self_hosted_customer_id
                FROM {_tbl("dim__self_hosted_licenses")}
                WHERE metronome_customer_id = @metronome_id
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY metronome_customer_id
                    ORDER BY is_license_verified_last_day DESC,
                             is_license_verified_last_7_days DESC,
                             last_verified_at_utc DESC
                ) = 1
            ),
            latest_rx_sh AS (
                SELECT MAX(received_at_utc) AS max_rx
                FROM {_tbl("stg_postgres__usage_snapshots")}
                WHERE self_hosted_customer_id IN (SELECT self_hosted_customer_id FROM sh_customer)
            ),
            sh_snap AS (
                SELECT SUM(run_rules) AS sh_run_rules
                FROM {_tbl("stg_postgres__usage_snapshots")} AS s
                CROSS JOIN latest_rx_sh AS lr
                WHERE s.self_hosted_customer_id IN (SELECT self_hosted_customer_id FROM sh_customer)
                  AND s.received_at_utc BETWEEN DATETIME_SUB(lr.max_rx, INTERVAL 1 HOUR)
                                            AND DATETIME_ADD(lr.max_rx, INTERVAL 1 HOUR)
            ),
            contract_start AS (
                SELECT MIN(CAST(contract_start_at_utc AS DATE)) AS start_date
                FROM {_tbl("dim__contracts")}
                WHERE metronome_customer_id = @metronome_id
                  AND is_active_contract = TRUE
            ),
            eval_direct AS (
                SELECT
                  DATE_TRUNC(rule_created_date, MONTH) AS month_start,
                  u.eval_category,
                  SUM(u.rules) AS rules
                FROM {_tbl("fct__evaluator_usage_daily")},
                UNNEST([
                  STRUCT('LLM - Online' AS eval_category, llm_online_rules AS rules),
                  STRUCT('LLM - Offline' AS eval_category, llm_offline_rules AS rules),
                  STRUCT('Code - Online' AS eval_category, code_online_rules AS rules),
                  STRUCT('Code - Offline' AS eval_category, code_offline_rules AS rules)
                ]) AS u
                CROSS JOIN contract_start
                WHERE organization_id IN (
                    SELECT organization_id FROM {_tbl("dim__organizations")}
                    WHERE metronome_customer_id = @metronome_id
                )
                AND rule_created_date >= contract_start.start_date
                GROUP BY 1, u.eval_category
            ),
            sh_fallback AS (
                -- Generate a monthly spine from contract start so the chart x-axis
                -- matches the other feature usage charts.  Snapshot value goes on the
                -- current month only; prior months are 0.  The WHERE guard keeps this
                -- empty for SaaS.
                SELECT
                    month_start,
                    'Run Rules' AS eval_category,
                    CASE
                        WHEN month_start = DATE_TRUNC(CURRENT_DATE(), MONTH)
                        THEN COALESCE((SELECT sh_run_rules FROM sh_snap), 0)
                        ELSE 0
                    END AS rules
                FROM contract_start,
                UNNEST(GENERATE_DATE_ARRAY(
                    DATE_TRUNC(contract_start.start_date, MONTH),
                    DATE_TRUNC(CURRENT_DATE(), MONTH),
                    INTERVAL 1 MONTH
                )) AS month_start
                WHERE COALESCE((SELECT sh_run_rules FROM sh_snap), 0) > 0
            )
            SELECT month_start, eval_category, rules FROM eval_direct
            UNION ALL
            -- Only include SH fallback when fct__evaluator_usage_daily has no rows
            SELECT month_start, eval_category, rules FROM sh_fallback
            WHERE NOT EXISTS (SELECT 1 FROM eval_direct LIMIT 1)
            ORDER BY 1, eval_category
        """
        job = client.query(q, job_config=_param("metronome_id", metronome_id))
        chart_data["evaluator_usage"] = _rows_to_dicts(job.result())
        _log.info("fetch_chart_data: evaluator_usage rows=%d", len(chart_data["evaluator_usage"]))
    except Exception as exc:
        _log.warning("fetch_chart_data: evaluator_usage query failed: %s", exc)
        chart_data["evaluator_usage"] = []

    # ------------------------------------------------------------------
    # 5. Contract metrics — KPI tiles for the commit usage slide (slide 23)
    #    Returns at most 1 row with: contract_end_date, pct_into_contract,
    #    pct_commit_used.  Total traces in contract is derived from
    #    cumulative_usage (last row) rather than re-querying.
    # ------------------------------------------------------------------
    try:
        q = f"""
            WITH contract AS (
                SELECT
                  CAST(contract_start_at_utc AS DATE) AS start_date,
                  CAST(contract_end_at_utc   AS DATE) AS end_date,
                  total_usage_in_contract,
                  COALESCE(activated_commit_contract_amount_usd, 0)
                    + COALESCE(customer_credits_amount_usd, 0)
                    + COALESCE(contract_credits_amount_usd, 0)
                    - COALESCE(subscription_charge_amount_usd, 0) AS commit_budget
                FROM {_tbl("dim__contracts")}
                WHERE metronome_customer_id = @metronome_id
                  AND is_active_contract = TRUE
                LIMIT 1
            )
            SELECT
              end_date AS contract_end_date,
              SAFE_DIVIDE(
                DATE_DIFF(CURRENT_DATE(), start_date, DAY),
                DATE_DIFF(end_date, start_date, DAY)
              ) AS pct_into_contract,
              SAFE_DIVIDE(total_usage_in_contract, NULLIF(commit_budget, 0)) AS pct_commit_used
            FROM contract
        """
        job = client.query(q, job_config=_param("metronome_id", metronome_id))
        rows = _rows_to_dicts(job.result())
        chart_data["contract_metrics"] = rows[0] if rows else {}
        _log.info("fetch_chart_data: contract_metrics row=%s", chart_data["contract_metrics"])
    except Exception as exc:
        _log.warning("fetch_chart_data: contract_metrics query failed: %s", exc)
        chart_data["contract_metrics"] = {}

    # ------------------------------------------------------------------
    # 6. Enablement stats — academy enrolled count, billable seats, and
    #    est. engineering headcount for the Enablement & Training slide.
    #    Uses Salesforce touchpoints (same source as the Hex YAML) so the
    #    count is scoped to this customer via metronome_customer_id.
    #    billable_seats is used as denominator (more meaningful than total
    #    estimated engineers for very large enterprises).
    # ------------------------------------------------------------------
    try:
        q = f"""
            WITH org AS (
                SELECT salesforce_account_id, organization_id
                FROM {_tbl("dim__organizations")}
                WHERE metronome_customer_id = @metronome_id
                  AND salesforce_account_id IS NOT NULL
            ),
            sf_acct AS (
                SELECT
                    sa.account_id,
                    COALESCE(
                        sa.zoominfo_engineering_employee_count,
                        CAST(sa.number_of_employees * 0.22 AS INT64)
                    ) AS est_engineering_headcount
                FROM {_tbl("fct__salesforce__account")} AS sa
                INNER JOIN org ON sa.account_id = org.salesforce_account_id
                LIMIT 1
            ),
            seats AS (
                SELECT MAX(u.billable_seats) AS billable_seats
                FROM {_tbl("fct__organization_usage_daily")} AS u
                WHERE u.organization_id IN (SELECT organization_id FROM org)
                  AND u.date_day >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
            ),
            academy AS (
                SELECT COUNT(DISTINCT t.contact_id) AS academy_enrolled
                FROM {_tbl("fct__salesforce__touchpoint")} AS t
                INNER JOIN {_tbl("fct__salesforce__contact")} AS c
                    ON t.contact_id = c.contact_id
                INNER JOIN sf_acct ON c.account_id = sf_acct.account_id
                WHERE t.source = 'Academy Enrollment'
            )
            SELECT
                COALESCE(sf.est_engineering_headcount, 0) AS est_engineering_headcount,
                COALESCE(a.academy_enrolled, 0)           AS academy_enrolled,
                COALESCE(s.billable_seats, 0)             AS billable_seats
            FROM sf_acct sf
            CROSS JOIN academy a
            CROSS JOIN seats s
        """
        job = client.query(q, job_config=_param("metronome_id", metronome_id))
        rows = _rows_to_dicts(job.result())
        chart_data["enablement_stats"] = rows[0] if rows else {}
        _log.info("fetch_chart_data: enablement_stats=%s", chart_data["enablement_stats"])
    except Exception as exc:
        _log.warning("fetch_chart_data: enablement_stats query failed: %s", exc)
        chart_data["enablement_stats"] = {}

    # ------------------------------------------------------------------
    # 7. Sign-ups by course — for the Academy table on slide 26
    # ------------------------------------------------------------------
    try:
        q = f"""
            WITH org AS (
                SELECT salesforce_account_id
                FROM {_tbl("dim__organizations")}
                WHERE metronome_customer_id = @metronome_id
                  AND salesforce_account_id IS NOT NULL
            ),
            sf_acct AS (
                SELECT sa.account_id
                FROM {_tbl("fct__salesforce__account")} AS sa
                INNER JOIN org ON sa.account_id = org.salesforce_account_id
                LIMIT 1
            )
            SELECT
                t.source_detail AS course_name,
                COUNT(*)        AS sign_ups
            FROM {_tbl("fct__salesforce__touchpoint")} AS t
            INNER JOIN {_tbl("fct__salesforce__contact")} AS c
                ON t.contact_id = c.contact_id
            INNER JOIN sf_acct ON c.account_id = sf_acct.account_id
            WHERE t.source = 'Academy Enrollment'
            GROUP BY t.source_detail
            ORDER BY sign_ups DESC
        """
        job = client.query(q, job_config=_param("metronome_id", metronome_id))
        chart_data["sign_ups_by_course"] = _rows_to_dicts(job.result())
        _log.info("fetch_chart_data: sign_ups_by_course rows=%d", len(chart_data["sign_ups_by_course"]))
    except Exception as exc:
        _log.warning("fetch_chart_data: sign_ups_by_course query failed: %s", exc)
        chart_data["sign_ups_by_course"] = []

    _log.info(
        "fetch_chart_data: done metronome_id=%s keys=%s",
        metronome_id,
        {k: (len(v) if isinstance(v, list) else bool(v)) for k, v in chart_data.items()},
    )
    return chart_data


def fetch_maturity_data(metronome_id: str) -> list[dict] | None:
    """Return maturity scorecard rows for the given Metronome customer ID.

    Runs the same logic as the Hex 'Customer Health & Maturity' + 'Maturity scorecard'
    SQL cells, combining both into a single parameterised BigQuery query.  The
    sh_snapshot_agg CTE is omitted (Hex-internal; cloud customers return 0 rows anyway).

    Returns a list of dicts with keys:
      dimension, stage, stage_label, metric_detail, recommendation,
      overall_avg_stage, overall_stage_label
    Returns None if the BQ client cannot be initialised.
    Returns [] if the customer has no matching org rows.
    """
    if not metronome_id:
        return None

    try:
        client = _client()
    except Exception as exc:
        _log.warning("BigQuery client init failed (fetch_maturity_data): %s", exc)
        return None

    from google.cloud import bigquery

    q = f"""
        WITH sh_customer AS (
            -- Returns 0 rows for SaaS customers; 1 row for SH customers.
            SELECT self_hosted_customer_id
            FROM {_tbl("dim__self_hosted_licenses")}
            WHERE metronome_customer_id = @metronome_id
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY metronome_customer_id
                ORDER BY is_license_verified_last_day DESC,
                         is_license_verified_last_7_days DESC,
                         last_verified_at_utc DESC
            ) = 1
        ),
        latest_rx_sh AS (
            SELECT MAX(received_at_utc) AS max_rx
            FROM {_tbl("stg_postgres__usage_snapshots")}
            WHERE self_hosted_customer_id IN (SELECT self_hosted_customer_id FROM sh_customer)
        ),
        sh_snapshot AS (
            -- Latest beacon cycle; 0 rows for SaaS customers.
            SELECT
                SUM(experiments)       AS sh_experiments,
                SUM(annotation_queues) AS sh_annotation_queues,
                SUM(users)             AS sh_users,
                SUM(active_pats_30d)   AS sh_active_pats_30d,
                SUM(run_rules)         AS sh_run_rules,
                SUM(evaluators)        AS sh_evaluators
            FROM {_tbl("stg_postgres__usage_snapshots")} AS s
            CROSS JOIN latest_rx_sh AS lr
            WHERE s.self_hosted_customer_id IN (SELECT self_hosted_customer_id FROM sh_customer)
              AND s.received_at_utc BETWEEN DATETIME_SUB(lr.max_rx, INTERVAL 1 HOUR)
                                        AND DATETIME_ADD(lr.max_rx, INTERVAL 1 HOUR)
        ),
        is_sh AS (
            -- sh_snapshot is an unkeyed aggregate — always 1 row even for SaaS.
            -- Use sh_customer (0 rows for SaaS, 1 for SH) as the authoritative flag.
            SELECT (SELECT COUNT(*) FROM sh_customer) > 0 AS is_self_hosted
        ),

        org AS (
            SELECT
                o.organization_id,
                o.organization_name,
                o.salesforce_account_id,
                o.current_arr_usd,
                o.plan_name
            FROM {_tbl("dim__organizations")} AS o
            WHERE o.metronome_customer_id = @metronome_id
              AND o.is_enterprise_org = TRUE
        ),

        sf AS (
            SELECT
                sa.number_of_employees,
                sa.zoominfo_engineering_employee_count,
                COALESCE(
                    sa.zoominfo_engineering_employee_count,
                    CAST(sa.number_of_employees * 0.22 AS NUMERIC)
                ) AS est_engineering_headcount
            FROM {_tbl("fct__salesforce__account")} AS sa
            WHERE sa.account_id IN (
                SELECT salesforce_account_id FROM org
                WHERE salesforce_account_id IS NOT NULL
            )
            LIMIT 1
        ),

        usage_latest AS (
            SELECT
                SUM(u.billable_trace_count)               AS monthly_traces,
                SUM(u.total_experiments)                  AS monthly_experiments,
                MAX(u.billable_seats)                     AS billable_seats,
                SUM(u.total_online_llm_evaluator_rules
                    + u.total_online_code_evaluator_rules) AS online_eval_rules_activity,
                SUM(u.total_add_to_aq_rules)              AS annotation_queue_rules,
                SUM(u.total_add_to_dataset_rules)         AS add_to_dataset_rules,
                COUNTIF(u.total_add_to_aq_rules > 0)      AS days_with_annotation_activity
            FROM {_tbl("fct__organization_usage_daily")} AS u
            INNER JOIN org AS o ON u.organization_id = o.organization_id
            WHERE u.date_day >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
        ),

        eval_rules AS (
            SELECT
                SUM(e.enabled_online_rules)  AS active_online_eval_rules,
                SUM(e.enabled_offline_rules) AS active_offline_eval_rules
            FROM {_tbl("fct__evaluator_usage_daily")} AS e
            INNER JOIN org AS o ON e.organization_id = o.organization_id
        ),

        active_users AS (
            SELECT COUNT(DISTINCT pv.segment_user_id) AS unique_active_users
            FROM {_tbl("int__page_views")} AS pv
            INNER JOIN org AS o ON pv.organization_id = o.organization_id
            WHERE pv.event_at_utc >= DATETIME_SUB(CURRENT_DATETIME(), INTERVAL 30 DAY)
              AND pv.segment_user_id IS NOT NULL
              AND pv.user_email NOT LIKE '%@langchain.dev'
        ),

        org_summary AS (
            SELECT
                MIN(o.organization_name) AS organization_name,
                MAX(o.current_arr_usd)   AS current_arr_usd,
                MAX(o.plan_name)         AS plan_name
            FROM org AS o
        ),

        customer_health_maturity AS (
            SELECT
                os.organization_name,
                COALESCE(ul.monthly_traces, 0) AS monthly_traces,
                -- SH overlay: use snapshot experiments for eval_offline when SH
                CASE WHEN ish.is_self_hosted
                     THEN COALESCE((SELECT sh_experiments FROM sh_snapshot), 0)
                     ELSE COALESCE(ul.monthly_experiments, 0)
                END AS monthly_experiments,
                COALESCE(ul.billable_seats, 0)              AS billable_seats,
                COALESCE(sf.est_engineering_headcount, 0)   AS est_engineering_headcount,
                SAFE_DIVIDE(
                    COALESCE(ul.billable_seats, 0),
                    NULLIF(sf.est_engineering_headcount, 0)
                ) AS eng_adoption_pct,
                -- SH: use snapshot active_pats_30d as proxy for unique_active_users
                CASE WHEN ish.is_self_hosted
                     THEN COALESCE((SELECT sh_active_pats_30d FROM sh_snapshot), 0)
                     ELSE COALESCE(au.unique_active_users, 0)
                END AS unique_active_users,
                -- SH: online eval rules not split; use NULL so dimension is skipped
                CASE WHEN ish.is_self_hosted THEN NULL
                     ELSE COALESCE(er.active_online_eval_rules, 0)
                END AS active_online_eval_rules,
                COALESCE(er.active_offline_eval_rules, 0)   AS active_offline_eval_rules,
                CASE WHEN ish.is_self_hosted THEN NULL
                     ELSE COALESCE(ul.online_eval_rules_activity, 0)
                END AS online_eval_rules_activity,
                -- SH: annotation queue (human alignment proxy)
                CASE WHEN ish.is_self_hosted
                     THEN COALESCE((SELECT sh_annotation_queues FROM sh_snapshot), 0)
                     ELSE COALESCE(ul.annotation_queue_rules, 0)
                END AS annotation_queue_rules,
                CASE WHEN ish.is_self_hosted THEN NULL
                     ELSE COALESCE(ul.add_to_dataset_rules, 0)
                END AS add_to_dataset_rules,
                CASE WHEN ish.is_self_hosted THEN NULL
                     ELSE COALESCE(ul.days_with_annotation_activity, 0)
                END AS days_with_annotation_activity,
                ish.is_self_hosted,

                -- Observability (same for SaaS and SH — traces always in fct__org_usage_daily)
                CASE
                    WHEN COALESCE(ul.monthly_traces, 0) >= 10000000 THEN 4
                    WHEN COALESCE(ul.monthly_traces, 0) >= 500000   THEN 3
                    WHEN COALESCE(ul.monthly_traces, 0) >= 10000    THEN 2
                    ELSE 1
                END AS observability_stage,
                -- Evaluation (Offline) — SH uses snapshot experiments
                CASE WHEN ish.is_self_hosted THEN
                    CASE
                        WHEN COALESCE((SELECT sh_experiments FROM sh_snapshot), 0) >= 100 THEN 4
                        WHEN COALESCE((SELECT sh_experiments FROM sh_snapshot), 0) >= 11  THEN 3
                        WHEN COALESCE((SELECT sh_experiments FROM sh_snapshot), 0) >= 1   THEN 2
                        ELSE 1
                    END
                ELSE
                    CASE
                        WHEN COALESCE(ul.monthly_experiments, 0) >= 100 THEN 4
                        WHEN COALESCE(ul.monthly_experiments, 0) >= 11  THEN 3
                        WHEN COALESCE(ul.monthly_experiments, 0) >= 1   THEN 2
                        ELSE 1
                    END
                END AS eval_offline_stage,
                -- Evaluation (Online) — NULL for SH (no online/offline split available)
                CASE WHEN ish.is_self_hosted THEN NULL
                    WHEN COALESCE(er.active_online_eval_rules, 0) > 5  THEN 4
                    WHEN COALESCE(er.active_online_eval_rules, 0) >= 1 THEN 3
                    WHEN COALESCE(ul.add_to_dataset_rules, 0) > 0
                      OR COALESCE(ul.annotation_queue_rules, 0) > 0    THEN 2
                    ELSE 1
                END AS eval_online_stage,
                -- Human Alignment — NULL for SH
                CASE WHEN ish.is_self_hosted THEN NULL
                    WHEN COALESCE(ul.days_with_annotation_activity, 0) >= 20 THEN 4
                    WHEN COALESCE(ul.days_with_annotation_activity, 0) >= 5  THEN 3
                    WHEN COALESCE(ul.days_with_annotation_activity, 0) >= 1  THEN 2
                    ELSE 1
                END AS human_alignment_stage,
                -- Production Intelligence (same threshold as Observability)
                CASE
                    WHEN COALESCE(ul.monthly_traces, 0) >= 10000000 THEN 4
                    WHEN COALESCE(ul.monthly_traces, 0) >= 500000   THEN 3
                    WHEN COALESCE(ul.monthly_traces, 0) >= 10000    THEN 2
                    ELSE 1
                END AS prod_intelligence_stage,
                -- Org Adoption — NULL for SH (no billable seats concept)
                CASE WHEN ish.is_self_hosted THEN NULL
                    WHEN SAFE_DIVIDE(
                        COALESCE(ul.billable_seats, 0),
                        NULLIF(sf.est_engineering_headcount, 0)
                    ) >= 0.50 THEN 4
                    WHEN SAFE_DIVIDE(
                        COALESCE(ul.billable_seats, 0),
                        NULLIF(sf.est_engineering_headcount, 0)
                    ) >= 0.20 THEN 3
                    WHEN SAFE_DIVIDE(
                        COALESCE(ul.billable_seats, 0),
                        NULLIF(sf.est_engineering_headcount, 0)
                    ) >= 0.05 THEN 2
                    ELSE 1
                END AS org_adoption_stage
            FROM org_summary AS os
            CROSS JOIN usage_latest AS ul
            CROSS JOIN eval_rules AS er
            CROSS JOIN active_users AS au
            CROSS JOIN is_sh AS ish
            LEFT JOIN sf ON TRUE
        )

        -- Maturity scorecard unpivot
        SELECT
            m.organization_name,
            stage_info.dimension,
            stage_info.stage,
            stage_info.metric_detail,
            CASE
                WHEN stage_info.stage = 1 THEN 'Exploring'
                WHEN stage_info.stage = 2 THEN 'Building'
                WHEN stage_info.stage = 3 THEN 'Operating'
                WHEN stage_info.stage = 4 THEN 'Scaling'
            END AS stage_label,
            CASE
                WHEN stage_info.dimension = 'Org Adoption' AND stage_info.stage <= 1
                    THEN 'Schedule enablement sessions with engineering leads and SMEs. Run LangChain Academy workshops to onboard new teams and drive broader adoption'
                WHEN stage_info.dimension = 'Org Adoption' AND stage_info.stage = 2
                    THEN 'Expand adoption through hands-on training for engineers. Identify internal champions and SMEs to lead enablement across teams'
                WHEN stage_info.dimension = 'Org Adoption' AND stage_info.stage = 3
                    THEN 'Continue education programs. Engage SMEs to standardize best practices across the org'
                WHEN stage_info.dimension = 'Org Adoption' AND stage_info.stage = 4
                    THEN 'Maintain high adoption. Leverage internal SMEs for peer-led training and advanced use case development'
                WHEN stage_info.stage <= 1
                    THEN 'Get traces flowing from production and run first evals'
                WHEN stage_info.stage = 2
                    THEN 'Move from offline-only to online evals. Close the loop between prod data and development'
                WHEN stage_info.stage = 3
                    THEN 'Stop drowning in traces. Let the platform surface what matters - deploy Insights Agent'
                ELSE 'Maintain and optimize current practices'
            END AS recommendation,
            ROUND(
                SAFE_DIVIDE(
                    COALESCE(m.observability_stage, 0)
                    + COALESCE(m.eval_offline_stage, 0)
                    + COALESCE(m.eval_online_stage, 0)
                    + COALESCE(m.human_alignment_stage, 0)
                    + COALESCE(m.prod_intelligence_stage, 0)
                    + COALESCE(m.org_adoption_stage, 0),
                    (CASE WHEN m.observability_stage    IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.eval_offline_stage   IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.eval_online_stage    IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.human_alignment_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.prod_intelligence_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.org_adoption_stage   IS NULL THEN 0 ELSE 1 END)
                ),
                1
            ) AS overall_avg_stage,
            CASE
                WHEN SAFE_DIVIDE(
                    COALESCE(m.observability_stage, 0) + COALESCE(m.eval_offline_stage, 0)
                    + COALESCE(m.eval_online_stage, 0) + COALESCE(m.human_alignment_stage, 0)
                    + COALESCE(m.prod_intelligence_stage, 0) + COALESCE(m.org_adoption_stage, 0),
                    (CASE WHEN m.observability_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.eval_offline_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.eval_online_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.human_alignment_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.prod_intelligence_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.org_adoption_stage IS NULL THEN 0 ELSE 1 END)
                ) < 1.5 THEN 'Exploring'
                WHEN SAFE_DIVIDE(
                    COALESCE(m.observability_stage, 0) + COALESCE(m.eval_offline_stage, 0)
                    + COALESCE(m.eval_online_stage, 0) + COALESCE(m.human_alignment_stage, 0)
                    + COALESCE(m.prod_intelligence_stage, 0) + COALESCE(m.org_adoption_stage, 0),
                    (CASE WHEN m.observability_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.eval_offline_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.eval_online_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.human_alignment_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.prod_intelligence_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.org_adoption_stage IS NULL THEN 0 ELSE 1 END)
                ) < 2.5 THEN 'Building'
                WHEN SAFE_DIVIDE(
                    COALESCE(m.observability_stage, 0) + COALESCE(m.eval_offline_stage, 0)
                    + COALESCE(m.eval_online_stage, 0) + COALESCE(m.human_alignment_stage, 0)
                    + COALESCE(m.prod_intelligence_stage, 0) + COALESCE(m.org_adoption_stage, 0),
                    (CASE WHEN m.observability_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.eval_offline_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.eval_online_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.human_alignment_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.prod_intelligence_stage IS NULL THEN 0 ELSE 1 END)
                    + (CASE WHEN m.org_adoption_stage IS NULL THEN 0 ELSE 1 END)
                ) < 3.5 THEN 'Operating'
                ELSE 'Scaling'
            END AS overall_stage_label
        FROM customer_health_maturity AS m
        CROSS JOIN UNNEST([
            STRUCT('Observability' AS dimension, m.observability_stage AS stage,
                   CONCAT(CAST(m.monthly_traces AS STRING), ' traces/month') AS metric_detail),
            STRUCT('Evaluation (Offline)', m.eval_offline_stage,
                   CONCAT(CAST(m.monthly_experiments AS STRING), ' experiments/month')),
            STRUCT('Evaluation (Online)', m.eval_online_stage,
                   CONCAT(CAST(m.active_online_eval_rules AS STRING), ' active online eval rules')),
            STRUCT('Human Alignment', m.human_alignment_stage,
                   CONCAT(CAST(m.days_with_annotation_activity AS STRING),
                          ' days with annotation activity (30d)')),
            STRUCT('Production Intelligence', m.prod_intelligence_stage,
                   CONCAT(CAST(m.monthly_traces AS STRING), ' traces/month')),
            STRUCT('Org Adoption', m.org_adoption_stage,
                   CONCAT(
                       CAST(m.billable_seats AS STRING), ' seats / ',
                       CAST(CAST(m.est_engineering_headcount AS INT64) AS STRING), ' est eng = ',
                       CAST(ROUND(COALESCE(m.eng_adoption_pct, 0) * 100, 1) AS STRING), '% adoption'
                   ))
        ]) AS stage_info
        WHERE stage_info.stage IS NOT NULL
        ORDER BY stage_info.stage ASC
    """

    try:
        job = client.query(
            q,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("metronome_id", "STRING", metronome_id)
                ]
            ),
        )
        rows = list(job.result())
        result = []
        for r in rows:
            result.append({
                "dimension":         r.dimension,
                "stage":             int(r.stage) if r.stage is not None else None,
                "stage_label":       r.stage_label,
                "metric_detail":     r.metric_detail,
                "recommendation":    r.recommendation,
                "overall_avg_stage": float(r.overall_avg_stage) if r.overall_avg_stage is not None else None,
                "overall_stage_label": r.overall_stage_label,
            })
        _log.info("fetch_maturity_data: %d rows for metronome_id=%s", len(result), metronome_id)
        return result
    except Exception as exc:
        _log.warning("fetch_maturity_data: query failed: %s", exc)
        return None
