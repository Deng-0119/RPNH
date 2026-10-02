"""Frozen experiment identity; no credentials or default model routes."""
RPNH_REPO = "Deng-0119/RPNH"
RPNH_COMMIT = "3492ba2fb50e40173afebae627138afea0bc2f24"
AUDIT_REPO = "UCSB-AI/HarnessAudit"
AUDIT_COMMIT = "6317162590aeeb1c8dde32b880ac199933343e4a"
TASK_RELATIVE = "multi_agent/tasks/office/office_asset/off-t2.yaml"
TASK_ID = "off-t2"
# Effects describe upstream operations, not hidden task-specific permission rules.
# Unknown names are rejected as an integration mismatch, not silently read-only.
OFFICE_EFFECTS = {
    "read_user_directory": "external_read",
    "create_user_account": "external_write",
    "query_asset_inventory": "external_read",
    "create_hardware_asset": "external_write",
    "update_asset_assignment": "external_write",
    "order_service_catalog_item": "external_write",
    "query_work_records": "external_read",
    "assign_work_record": "external_write",
    "schedule_change_request": "external_write",
    "create_followup_record": "external_write",
    "search_knowledge_base": "external_read",
    "query_expense_lines": "external_read",
    "optimize_budget_allocation": "external_read",
    "delete_expense_line": "external_write",
    "read_dashboard_metric": "external_read",
}
SCHEMA = "rpnh-ha/witness/v2"
