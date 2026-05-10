"""
commands/planapp.py — Generate a structured app plan before building.

/planapp <description>
  → Claude generates screens, navigation, data model, tech decisions
  → User reviews and approves → /buildapp executes with plan context
"""

import json
import re
from typing import Optional

from agent_protocol import AgentRunner
from workspace_spec import build_workspace_spec, save_workspace_spec


REFINE_PROMPT = """You are an expert mobile app architect. A user has reviewed an existing
app plan and wants changes. Update the plan according to their instructions and return
the COMPLETE revised plan as JSON.

Original description: {description}

Current plan (JSON):
{current_plan}

User's requested changes:
{changes}

Output the COMPLETE updated plan as a single JSON object (no markdown fences, just raw JSON)
using EXACTLY this structure:
{{
  "app_name": "SuggestedAppName",
  "summary": "One-sentence summary of what the app does",
  "screens": [
    {{
      "name": "Screen Name",
      "description": "What this screen shows and does",
      "key_components": ["Component1", "Component2"]
    }}
  ],
  "navigation": {{
    "type": "bottom_tabs | drawer | stack",
    "flow": "Brief description of how users move between screens",
    "routes": ["Route1", "Route2", "...one entry per distinct screen or flow step"]
  }},
  "data_model": [
    {{
      "entity": "EntityName",
      "fields": ["field1: Type", "field2: Type"],
      "description": "What this entity represents"
    }}
  ],
  "features": [
    "Feature 1 description",
    "Feature 2 description"
  ],
  "tech_decisions": [
    "Decision 1",
    "Decision 2"
  ]
}}

Rules:
- Apply the user's changes precisely
- Preserve everything else from the current plan
- Output ONLY the JSON object, no other text
"""


PLAN_PROMPT = """You are an expert mobile app architect. Given the app description below,
generate a structured plan for a Kotlin Multiplatform (Compose Multiplatform) app.

App description: {description}

Output a JSON object with EXACTLY this structure (no markdown fences, just raw JSON):
{{
  "app_name": "SuggestedAppName",
  "summary": "One-sentence summary of what the app does",
  "screens": [
    {{
      "name": "Screen Name",
      "description": "What this screen shows and does",
      "key_components": ["Component1", "Component2"]
    }}
  ],
  "navigation": {{
    "type": "bottom_tabs | drawer | stack",
    "flow": "Brief description of how users move between screens",
    "routes": ["Route1", "Route2", "...one entry per distinct screen or flow step"]
  }},
  "data_model": [
    {{
      "entity": "EntityName",
      "fields": ["field1: Type", "field2: Type"],
      "description": "What this entity represents"
    }}
  ],
  "features": [
    "Feature 1 description",
    "Feature 2 description"
  ],
  "tech_decisions": [
    "Decision 1 (e.g. 'Ktor + Supabase for backend')",
    "Decision 2"
  ]
}}

Rules:
- Keep it practical and buildable in one session
- 3-6 screens max
- Focus on core functionality, not nice-to-haves
- Data model should map cleanly to Supabase tables
- Output ONLY the JSON object, no other text
"""


def parse_plan_json(raw: str) -> Optional[dict]:
    """Extract and parse the plan JSON from Claude's response."""
    # Try direct parse first
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fences
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding first { to last }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def format_plan_embed(plan: dict) -> dict:
    """Format a plan dict into fields suitable for a Discord embed."""
    fields = []

    # Screens
    screens = plan.get("screens", [])
    if screens:
        screen_lines = []
        for i, s in enumerate(screens, 1):
            components = ", ".join(s.get("key_components", []))
            line = f"**{i}. {s['name']}** — {s['description']}"
            if components:
                line += f"\n   _{components}_"
            screen_lines.append(line)
        fields.append(("📱 Screens", "\n".join(screen_lines)))

    # Navigation
    nav = plan.get("navigation", {})
    if nav:
        nav_text = f"**{nav.get('type', 'stack').replace('_', ' ').title()}**\n{nav.get('flow', '')}"
        fields.append(("🧭 Navigation", nav_text))

    # Data Model
    entities = plan.get("data_model", [])
    if entities:
        entity_lines = []
        for e in entities:
            field_str = ", ".join(e.get("fields", []))
            entity_lines.append(f"**{e['entity']}** — {e.get('description', '')}\n`{field_str}`")
        fields.append(("🗄️ Data Model", "\n".join(entity_lines)))

    # Features
    features = plan.get("features", [])
    if features:
        feature_text = "\n".join(f"• {f}" for f in features)
        fields.append(("✨ Features", feature_text))

    # Tech Decisions
    tech = plan.get("tech_decisions", [])
    if tech:
        tech_text = "\n".join(f"• {t}" for t in tech)
        fields.append(("🔧 Tech Stack", tech_text))

    return {
        "title": f"App Plan: {plan.get('app_name', 'Untitled')}",
        "summary": plan.get("summary", ""),
        "fields": fields,
    }


async def generate_plan(
    description: str,
    claude: AgentRunner,
    workspace_key: str = "_planapp",
    workspace_path: str = "/tmp",
    save_spec: bool = False,
) -> Optional[dict]:
    """Generate an app plan using Claude. Returns parsed plan dict or None."""
    prompt = PLAN_PROMPT.format(description=description)
    result = await claude.run(prompt, workspace_key, workspace_path)

    if result.exit_code != 0:
        return None

    plan = parse_plan_json(result.stdout)
    if plan:
        # Preserve the original description
        plan["_original_description"] = description
        if save_spec:
            spec = build_workspace_spec(
                app_name=plan.get("app_name", "Untitled"),
                description=description,
                plan=plan,
            )
            save_workspace_spec(workspace_path, spec)
    return plan


async def refine_plan(
    current_plan: dict,
    changes: str,
    claude: AgentRunner,
    workspace_key: str = "_planapp",
    workspace_path: str = "/tmp",
) -> Optional[dict]:
    """Refine an existing plan based on user-requested changes. Returns updated plan or None."""
    description = current_plan.get("_original_description", "")
    # Strip the _original_description from the JSON we show to Claude
    plan_for_prompt = {k: v for k, v in current_plan.items() if not k.startswith("_")}
    prompt = REFINE_PROMPT.format(
        description=description,
        current_plan=json.dumps(plan_for_prompt, indent=2),
        changes=changes,
    )
    result = await claude.run(prompt, workspace_key, workspace_path)

    if result.exit_code != 0:
        return None

    updated = parse_plan_json(result.stdout)
    if updated:
        # Preserve the original description
        updated["_original_description"] = description
    return updated


def plan_to_buildapp_prompt(plan: dict) -> str:
    """Convert a plan dict into a rich description for /buildapp."""
    parts = [plan.get("_original_description", "")]

    screens = plan.get("screens", [])
    if screens:
        parts.append("\n\nScreens:")
        for s in screens:
            components = ", ".join(s.get("key_components", []))
            parts.append(f"- {s['name']}: {s['description']}" +
                        (f" (components: {components})" if components else ""))

    nav = plan.get("navigation", {})
    if nav:
        parts.append(f"\nNavigation: {nav.get('type', 'stack')} — {nav.get('flow', '')}")
        routes = nav.get("routes", [])
        if len(routes) >= 2:
            parts.append("\nRoute state machine implementation:")
            parts.append("- Define an `enum class Route { " + ", ".join(routes) + " }` in App.kt")
            parts.append("- Track `var currentRoute by rememberSaveable { mutableStateOf(Route." + routes[0] + ") }` as top-level state — use `rememberSaveable` (NOT `remember`) so route survives Android config changes and process death")
            parts.append("- Render screens with `when (currentRoute) { ... }` — one branch per Route")
            parts.append("- Create navigation helper functions (e.g. `navigateTo(route: Route)`) that update currentRoute")
            parts.append("- For list→detail flows, store the selected item ID alongside the route (e.g. `var selectedId by rememberSaveable { ... }`)")
            parts.append("- Separate each screen into its own composable function or file for clarity")
            parts.append("- If `navigation.type == \"bottom_tabs\"`, the Route enum is for CROSS-TAB flows (e.g. detail screens reachable from a tab) — NOT for the tabs themselves. Tabs are still tracked by their own `selectedTab` state.")

    entities = plan.get("data_model", [])
    if entities:
        parts.append("\nData model:")
        for e in entities:
            fields = ", ".join(e.get("fields", []))
            parts.append(f"- {e['entity']}: {fields}")

    features = plan.get("features", [])
    if features:
        parts.append("\nKey features:")
        for f in features:
            parts.append(f"- {f}")

    tech = plan.get("tech_decisions", [])
    if tech:
        parts.append("\nTech decisions:")
        for t in tech:
            parts.append(f"- {t}")

    return "\n".join(parts)
