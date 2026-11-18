"""Human-readable labels without changing observed accessibility identities."""


def control_label(element: dict, elements: list[dict] | None = None) -> str:
    for key in ("name", "field_label"):
        if str(element.get(key) or "").strip():
            return str(element[key]).strip()
    group = element.get("control_group")
    if group and elements:
        inputs = [item for item in elements if item.get("control_group") == group
                  and item.get("role") in {"textbox", "input", "combobox", "searchbox", "spinbutton"}]
        labels = {str(item.get("name") or "").strip() for item in elements
                  if item.get("control_group") == group and item.get("role") == "StaticText"
                  and str(item.get("xpath") or "").endswith("/label") and item.get("name")}
        if len(inputs) == 1 and len(labels) == 1:
            return labels.pop()
    if element.get("input_type") == "password":
        return "Password"
    return str(element.get("element_code") or "Input")
