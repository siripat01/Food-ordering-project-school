from app.integrations.agent.tools import CustomerToolFactory, StaffToolFactory


def test_customer_agent_has_only_customer_operations() -> None:
    assert CustomerToolFactory.TOOL_NAMES == {
        "list_products",
        "create_own_order",
        "view_own_orders",
        "cancel_eligible_own_order",
    }
    forbidden = {
        "update_order_status",
        "delete_product",
        "delete_user",
        "update_user_role",
        "find_user",
    }
    assert CustomerToolFactory.TOOL_NAMES.isdisjoint(forbidden)
    assert CustomerToolFactory.TOOL_NAMES.isdisjoint(StaffToolFactory.TOOL_NAMES)
