"""Record ids in URLs must not split one screen into many states."""

from core.agents.application_discovery.fingerprint import (
    canonical_route,
    compute_fingerprint,
    functional_page_key,
    is_in_page_content_control,
    observed_page_label,
    page_section_route,
    page_content_signature,
    shared_component_keys,
)

SNAPSHOT = 'uid=1_0 RootWebArea "Order" url="x"\nuid=1_1 heading "Order"'


def test_query_record_ids_collapse_to_one_state() -> None:
    assert compute_fingerprint("https://a.test/order?id=12", SNAPSHOT) == compute_fingerprint(
        "https://a.test/order?id=9876", SNAPSHOT
    )
    assert compute_fingerprint(
        "https://a.test/order?id=3f2504e0-4f89-11d3-9a0c-0305e82c3301", SNAPSHOT
    ) == compute_fingerprint("https://a.test/order?id=12", SNAPSHOT)


def test_meaningful_query_values_still_distinguish_states() -> None:
    assert compute_fingerprint("https://a.test/list?tab=open", SNAPSHOT) != compute_fingerprint(
        "https://a.test/list?tab=closed", SNAPSHOT
    )


def test_canonical_route_drops_transport_parameters_but_keeps_screen_state() -> None:
    first = "https://a.test/orders?tab=open&utm_source=email&token=one"
    second = "https://a.test/orders?token=two&tab=open&utm_source=ad"
    assert canonical_route(first) == "/orders?tab=open"
    assert canonical_route(first) == canonical_route(second)
    assert canonical_route("https://a.test/orders/") == canonical_route("https://a.test/orders")
    assert canonical_route("https://a.test/orders?tab=closed") == "/orders?tab=closed"
    assert canonical_route("https://a.test/#/orders/123") == "/#/orders/{id}"


def test_functional_key_keeps_meaningful_query_state() -> None:
    nodes = [
        {"role": "RootWebArea", "name": "Orders", "url": "https://a.test/orders?tab=open"},
        {"role": "heading", "name": "Orders"},
    ]
    assert functional_page_key("https://a.test/orders?tab=open", nodes) != functional_page_key(
        "https://a.test/orders?tab=closed", nodes
    )


def test_shared_shell_controls_do_not_change_content_signature() -> None:
    first = [
        {"role": "RootWebArea", "name": "Orders", "url": "https://a.test/orders"},
        {"role": "link", "name": "Home", "url": "/", "locator_scope": "navigation"},
        {"role": "link", "name": "Help", "url": "/help", "locator_scope": "footer"},
        {"role": "button", "name": "Create order"},
    ]
    second = [
        *first[:1],
        {"role": "link", "name": "Home", "url": "/", "locator_scope": "navigation"},
        {"role": "link", "name": "Privacy", "url": "/privacy", "locator_scope": "footer"},
        *first[3:],
    ]
    assert shared_component_keys(first)
    assert page_content_signature(first) == page_content_signature(second)


def test_sidebar_menu_noise_shares_one_functional_page() -> None:
    dashboard = [
        {"role": "RootWebArea", "name": "Kitchen POS", "url": "https://a.test/"},
        {"role": "heading", "name": "Menu"},
        {"role": "heading", "name": "Dashboard"},
    ]
    menu_open = [
        {"role": "RootWebArea", "name": "Menu | Kitchen POS", "url": "https://a.test/"},
        {"role": "heading", "name": "Menu"},
        {"role": "button", "name": "Pizza"},
    ]
    menu_again = [
        {"role": "RootWebArea", "name": "Menu | Categories", "url": "https://a.test/"},
        {"role": "heading", "name": "Menu"},
        {"role": "button", "name": "Pasta"},
    ]
    orders = [
        {"role": "RootWebArea", "name": "Orders | Kitchen POS", "url": "https://a.test/"},
        {"role": "heading", "name": "Menu"},
        {"role": "heading", "name": "Orders"},
    ]
    assert functional_page_key("https://a.test/", menu_open) == functional_page_key(
        "https://a.test/", menu_again
    )
    assert functional_page_key("https://a.test/", dashboard) != functional_page_key(
        "https://a.test/", menu_open
    )
    assert functional_page_key("https://a.test/", dashboard) != functional_page_key(
        "https://a.test/", orders
    )
    assert observed_page_label(dashboard, "https://a.test/") == "Dashboard"
    assert observed_page_label(menu_open, "https://a.test/") == "Menu"
    assert observed_page_label(orders, "https://a.test/") == "Orders"


def test_menu_catalog_items_are_one_page() -> None:
    boiled = [
        {"role": "RootWebArea", "name": "Boiled Egg", "url": "https://a.test/menu/boiled-egg"},
        {"role": "heading", "name": "Boiled Egg"},
        {"role": "button", "name": "Add to Cart"},
    ]
    coffee = [
        {"role": "RootWebArea", "name": "Coffee Sachet", "url": "https://a.test/menu/coffee-sachet"},
        {"role": "heading", "name": "Coffee Sachet"},
    ]
    dashboard = [
        {"role": "RootWebArea", "name": "Dashboard", "url": "https://a.test/dashboard"},
        {"role": "heading", "name": "Dashboard"},
    ]
    assert page_section_route("https://a.test/menu/boiled-egg") == "/menu"
    assert functional_page_key("https://a.test/menu/boiled-egg", boiled) == functional_page_key(
        "https://a.test/menu/coffee-sachet", coffee
    )
    assert functional_page_key("https://a.test/dashboard", dashboard) != functional_page_key(
        "https://a.test/menu", boiled
    )
    assert observed_page_label(boiled, "https://a.test/menu/boiled-egg") == "Menu"
    assert is_in_page_content_control("button", "Boiled Egg", None, "https://a.test/menu")
    assert is_in_page_content_control("button", "Add to Cart", None, "https://a.test/menu")
    assert is_in_page_content_control("button", "Evening snacks", None, "https://a.test/menu")
    assert is_in_page_content_control("button", "2", None, "https://crm.test/invoices")
    assert is_in_page_content_control("button", "Unread 0", None, "https://crm.test/inbox")
    assert not is_in_page_content_control("link", "Orders", "https://a.test/orders", "https://a.test/dashboard")
    assert not is_in_page_content_control("link", "Cart", "https://a.test/cart", "https://a.test/menu")
    assert not is_in_page_content_control("link", "Invoices", "https://crm.test/invoices", "https://crm.test/home")
    assert not is_in_page_content_control("link", "Customers", "/customers", "https://crm.test/dashboard")
    assert not is_in_page_content_control("button", "Details", None, "https://a.test/")
    assert not is_in_page_content_control("button", "Dashboard", None, "https://a.test/menu")
    assert not is_in_page_content_control("button", "Open workspace", None, "https://crm.test/home")
    from core.agents.application_discovery.fingerprint import repeated_in_page_control

    assert repeated_in_page_control(
        "Plan This Slot",
        [{"role": "button", "name": "Plan This Slot"}] * 3,
    )
    assert not repeated_in_page_control(
        "Browse Menu",
        [{"role": "button", "name": "Browse Menu"}, {"role": "link", "name": "Menu", "url": "/menu"}],
    )


def test_gated_reveal_is_generic_and_optional() -> None:
    from core.agents.application_discovery.fingerprint import (
        gated_probe_kind,
        is_collection_mutation_action,
        is_gated_collection_entry,
        is_gated_reveal_action,
    )

    assert is_gated_reveal_action("Add to Cart")
    assert is_gated_reveal_action("Add to wishlist")
    assert not is_gated_reveal_action("Invoices")
    assert not is_gated_reveal_action("Checkout")
    assert not is_gated_reveal_action("Park this dish")
    assert is_gated_collection_entry("", "Shopping cart", None)
    assert is_gated_collection_entry("Bag", None, "/bag")
    assert not is_gated_collection_entry("Clear cart", None, "/cart")
    assert not is_gated_collection_entry("Checkout", None, "/checkout")
    assert is_collection_mutation_action("Clear cart")
    assert is_collection_mutation_action("Checkout")
    assert gated_probe_kind("Add to bag", None, None, "https://shop.test/products") == "reveal"
    assert gated_probe_kind("", "Cart", None, "https://shop.test/products") == "collection"
    assert gated_probe_kind("Cart", None, "https://shop.test/cart", "https://shop.test/dashboard") is None
    assert gated_probe_kind("", None, "https://shop.test/cart", "https://shop.test/dashboard") is None
    assert gated_probe_kind("", "Cart", None, "https://shop.test/cart") is None
    assert gated_probe_kind("Users", None, "/users", "https://crm.test/home") is None
    assert not is_in_page_content_control(
        "link", "", "https://shop.test/cart", "https://shop.test/dashboard"
    )


def test_dom_hrefs_missing_from_snapshot_are_merged() -> None:
    from core.tool_gateway.snapshot import merge_dom_hrefs

    nodes = [
        {"role": "link", "name": "Menu", "url": "https://shop.test/menu"},
    ]
    merged = merge_dom_hrefs(
        nodes,
        [{"name": "", "url": "https://shop.test/cart"}, {"name": "Menu", "url": "https://shop.test/menu"}],
    )
    assert any(node.get("url") == "https://shop.test/cart" for node in merged)
    assert sum(1 for node in merged if "menu" in str(node.get("url", "")).lower()) == 1
