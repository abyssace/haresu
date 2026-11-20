"""Example harnesses. 'support' runs completely with the mock model."""

from __future__ import annotations

import copy

from .spec import NODE_TYPES
from .store import DEFAULT_GATE


def _n(id_: str, type_: str, label: str, x: int, y: int, **data) -> dict:
    return {"id": id_, "type": type_, "label": label, "position": {"x": x, "y": y},
            "data": {**copy.deepcopy(NODE_TYPES[type_]["defaults"]), **data}}


def _e(source: str, target: str) -> dict:
    return {"id": f"e-{source}-{target}", "source": source, "target": target}


def blank() -> tuple[dict, dict]:
    """Empty canvas: the user builds everything from scratch."""
    spec = {"name": "New harness", "description": "", "nodes": [], "edges": []}
    return spec, {"gate": dict(DEFAULT_GATE), "judge": {"model": "mock"}, "cases": []}


def soporte() -> tuple[dict, dict]:
    """Support for an online store. The initial version is missing the routing for refunds."""
    prompt = ("You are the {area} agent of an online store. Before answering, search the shared "
              "context with search_context and answer only with what you find there, in one or two sentences. "
              "If there is no information, say so.")
    spec = {
        "name": "Store support",
        "description": "Three support agents behind a router, with shared context per area.",
        "nodes": [
            _n("input", "input", "Input", 0, 300),
            _n("router", "router", "Router", 240, 300, default="ag_general", rules=[
                {"id": "r1", "keywords": ["invoice", "billing", "payment", "card"], "target": "ag_payments"},
                {"id": "r2", "keywords": ["shipping", "package", "delivery", "tracking"], "target": "ag_shipping"},
            ]),
            _n("ag_payments", "agent", "Payments agent", 560, 80, system_prompt=prompt.format(area="payments")),
            _n("ag_shipping", "agent", "Shipping agent", 560, 300, system_prompt=prompt.format(area="shipping")),
            _n("ag_general", "agent", "General agent", 560, 520, system_prompt=prompt.format(area="general support")),
            _n("output", "output", "Output", 900, 300),
            _n("ctx_payments", "context", "Context: payments", 240, 20, docs=[
                {"id": "pag-1", "title": "Double billing",
                 "text": "If you see a duplicate charge, the second charge is reversed automatically within 72 hours."},
                {"id": "pag-2", "title": "Invoices",
                 "text": "You can correct the details of an invoice from My orders during the 30 days after the purchase."},
                {"id": "pag-3", "title": "Refunds",
                 "text": "Refunds (money back) are processed in 5 to 10 business days to the same payment method."},
            ]),
            _n("ctx_shipping", "context", "Context: shipping", 240, 560, docs=[
                {"id": "env-1", "title": "Tracking",
                 "text": "Package tracking is checked with the tracking number that arrives by email when the order ships."},
                {"id": "env-2", "title": "Delivery times",
                 "text": "Delivery takes 2 to 5 business days depending on the city."},
                {"id": "env-3", "title": "Damaged package",
                 "text": "If the package arrived damaged, send photos within the first 48 hours so we can replace it."},
            ]),
            _n("ctx_general", "context", "Context: account", 560, 740, docs=[
                {"id": "gen-1", "title": "Password",
                 "text": "If you forgot your password, request a recovery link from the sign-in screen."},
                {"id": "gen-2", "title": "Account details",
                 "text": "You can change your account email in Profile, Personal details section."},
                {"id": "gen-3", "title": "Opening hours",
                 "text": "Support hours are Monday to Saturday, 8 am to 8 pm."},
            ]),
            _n("search", "tool", "search_context", 900, 80, tool="search_context"),
            _n("governance", "policy", "Global governance", 900, 540, locked=True, max_steps=6, max_tool_calls=8,
               max_output_chars=1200, redact_patterns=[r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"]),
            _n("meta", "meta", "Meta-agent", 900, 740),
        ],
        "edges": [
            _e("input", "router"),
            _e("router", "ag_payments"), _e("router", "ag_shipping"), _e("router", "ag_general"),
            _e("ctx_payments", "ag_payments"), _e("ctx_shipping", "ag_shipping"), _e("ctx_general", "ag_general"),
            _e("search", "ag_payments"), _e("search", "ag_shipping"), _e("search", "ag_general"),
            _e("ag_payments", "output"), _e("ag_shipping", "output"), _e("ag_general", "output"),
        ],
    }

    def case(id_: str, split: str, text: str, contains: list[str], route: str, not_contains: list[str] | None = None) -> dict:
        expect = {"contains": contains, "route": route}
        if not_contains:
            expect["not_contains"] = not_contains
        return {"id": id_, "split": split, "input": text, "expect": expect}

    cases = [
        case("d1", "dev", "I was charged twice, a duplicate payment for my order", ["72 hours"], "ag_payments"),
        case("d2", "dev", "Where is my package? I want the tracking", ["tracking number"], "ag_shipping"),
        case("d3", "dev", "I want a refund for my purchase", ["5 to 10 business days"], "ag_payments"),
        case("d4", "dev", "How do I get my money back for a product?", ["5 to 10 business days"], "ag_payments"),
        case("d5", "dev", "I forgot my password", ["recovery link"], "ag_general"),
        case("d6", "dev", "My email is ana@example.com and my package arrived damaged", ["48 hours"], "ag_shipping",
             ["ana@example.com"]),
        case("h1", "heldout", "I need the refund for a charge", ["5 to 10 business days"], "ag_payments"),
        case("h2", "heldout", "I would like my money back, please", ["5 to 10 business days"], "ag_payments"),
        case("h3", "heldout", "The invoice came out with wrong details", ["30 days"], "ag_payments"),
        case("h4", "heldout", "How long does delivery take to my city?", ["2 to 5 business days"], "ag_shipping"),
        case("h5", "heldout", "I want to change the email of my account", ["Personal details"], "ag_general"),
    ]
    return spec, {"gate": dict(DEFAULT_GATE), "judge": {"model": "mock"}, "cases": cases}


EXAMPLES = {"blank": blank, "support": soporte}
