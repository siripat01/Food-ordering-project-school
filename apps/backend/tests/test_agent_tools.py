from app.integrations.agent.service import CUSTOMER_SYSTEM_PROMPT
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


def test_customer_agent_prompt_has_brand_and_security_boundaries() -> None:
    assert "HiwKaw (หิวข้าว)" in CUSTOMER_SYSTEM_PROMPT
    assert "provided customer tools" in CUSTOMER_SYSTEM_PROMPT
    assert "Never invent" in CUSTOMER_SYSTEM_PROMPT
    assert "identity, authorization, and final prices" in CUSTOMER_SYSTEM_PROMPT
