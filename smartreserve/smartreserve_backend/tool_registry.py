import enum
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

class PermissionTier(enum.Enum):
    T0 = "READ_ONLY"
    T1 = "COMFORT"
    T2 = "CONSEQUENTIAL"
    T3 = "SAFETY_CRITICAL"

@dataclass
class Tool:
    name: str
    description: str
    tier: PermissionTier
    parameters: dict
    handler: str
    requires_confirmation: bool = False
    blocked_while_moving: bool = False

class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        self._audit_log: List[Dict[str, Any]] = []
        self._register_default_tools()

    def _register_default_tools(self):
        # T0 Tools
        self._tools["vehicle.get_status"] = Tool(
            name="vehicle.get_status",
            description="Get battery SoC, range, speed, all telemetry",
            tier=PermissionTier.T0,
            parameters={"type": "object", "properties": {}},
            handler="handle_get_status"
        )
        self._tools["vehicle.get_diagnostics"] = Tool(
            name="vehicle.get_diagnostics",
            description="Get component health, service status, insights",
            tier=PermissionTier.T0,
            parameters={"type": "object", "properties": {}},
            handler="handle_get_diagnostics"
        )
        self._tools["vehicle.get_range"] = Tool(
            name="vehicle.get_range",
            description="Get estimated range with current charge",
            tier=PermissionTier.T0,
            parameters={"type": "object", "properties": {}},
            handler="handle_get_range"
        )
        self._tools["vehicle.get_tyre_pressure"] = Tool(
            name="vehicle.get_tyre_pressure",
            description="Get all tyre pressures",
            tier=PermissionTier.T0,
            parameters={"type": "object", "properties": {}},
            handler="handle_get_tyre_pressure"
        )
        self._tools["charging.find_stations"] = Tool(
            name="charging.find_stations",
            description="Find nearby compatible charging stations",
            tier=PermissionTier.T0,
            parameters={"type": "object", "properties": {"radius_km": {"type": "number", "default": 5}}},
            handler="handle_find_stations"
        )
        self._tools["charging.recommend"] = Tool(
            name="charging.recommend",
            description="Get AI-ranked priority list of best stations",
            tier=PermissionTier.T0,
            parameters={"type": "object", "properties": {}},
            handler="handle_recommend_stations"
        )
        self._tools["charging.get_station_details"] = Tool(
            name="charging.get_station_details",
            description="Get specific station info",
            tier=PermissionTier.T0,
            parameters={"type": "object", "properties": {"station_id": {"type": "string"}}, "required": ["station_id"]},
            handler="handle_get_station_details"
        )
        self._tools["charging.get_session_status"] = Tool(
            name="charging.get_session_status",
            description="Get current charging session status",
            tier=PermissionTier.T0,
            parameters={"type": "object", "properties": {}},
            handler="handle_get_session_status"
        )
        self._tools["navigation.get_eta"] = Tool(
            name="navigation.get_eta",
            description="Get ETA to destination",
            tier=PermissionTier.T0,
            parameters={"type": "object", "properties": {"destination": {"type": "string"}}, "required": ["destination"]},
            handler="handle_get_eta"
        )

        # T1 Tools
        self._tools["comfort.set_ac_temperature"] = Tool(
            name="comfort.set_ac_temperature",
            description="Set AC temperature",
            tier=PermissionTier.T1,
            parameters={"type": "object", "properties": {"temperature": {"type": "number"}}, "required": ["temperature"]},
            handler="handle_set_ac_temperature"
        )
        self._tools["comfort.set_seat_heating"] = Tool(
            name="comfort.set_seat_heating",
            description="Toggle seat heating",
            tier=PermissionTier.T1,
            parameters={"type": "object", "properties": {"level": {"type": "integer", "description": "0-3"}}, "required": ["level"]},
            handler="handle_set_seat_heating"
        )
        self._tools["comfort.set_ambient_light"] = Tool(
            name="comfort.set_ambient_light",
            description="Set ambient LED colour",
            tier=PermissionTier.T1,
            parameters={"type": "object", "properties": {"color": {"type": "string"}}, "required": ["color"]},
            handler="handle_set_ambient_light"
        )
        self._tools["media.set_volume"] = Tool(
            name="media.set_volume",
            description="Set media volume",
            tier=PermissionTier.T1,
            parameters={"type": "object", "properties": {"volume": {"type": "integer", "description": "0-100"}}, "required": ["volume"]},
            handler="handle_set_volume"
        )

        # T2 Tools
        self._tools["charging.reserve_slot"] = Tool(
            name="charging.reserve_slot",
            description="Reserve a 30-min charging slot",
            tier=PermissionTier.T2,
            parameters={"type": "object", "properties": {"station_id": {"type": "string"}, "time": {"type": "string"}}, "required": ["station_id"]},
            handler="handle_reserve_slot",
            requires_confirmation=True,
            blocked_while_moving=True
        )
        self._tools["charging.cancel_reservation"] = Tool(
            name="charging.cancel_reservation",
            description="Cancel active reservation",
            tier=PermissionTier.T2,
            parameters={"type": "object", "properties": {"reservation_id": {"type": "string"}}, "required": ["reservation_id"]},
            handler="handle_cancel_reservation",
            requires_confirmation=True,
            blocked_while_moving=True
        )
        self._tools["vehicle.remote_start_charge"] = Tool(
            name="vehicle.remote_start_charge",
            description="Start charging session remotely",
            tier=PermissionTier.T2,
            parameters={"type": "object", "properties": {}},
            handler="handle_remote_start_charge",
            requires_confirmation=True
        )
        self._tools["vehicle.unlock"] = Tool(
            name="vehicle.unlock",
            description="Unlock the vehicle",
            tier=PermissionTier.T2,
            parameters={"type": "object", "properties": {}},
            handler="handle_unlock",
            requires_confirmation=True,
            blocked_while_moving=True
        )

        # T3 Tools
        self._tools["vehicle.set_drive_mode"] = Tool(
            name="vehicle.set_drive_mode",
            description="BLOCKED - safety critical",
            tier=PermissionTier.T3,
            parameters={"type": "object", "properties": {"mode": {"type": "string"}}},
            handler="handle_set_drive_mode",
            blocked_while_moving=True
        )
        self._tools["vehicle.emergency_brake"] = Tool(
            name="vehicle.emergency_brake",
            description="BLOCKED - safety critical",
            tier=PermissionTier.T3,
            parameters={"type": "object", "properties": {}},
            handler="handle_emergency_brake",
            blocked_while_moving=True
        )

    def get_tool(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def get_available_tools(self, include_tiers: List[PermissionTier] = None) -> List[Tool]:
        if include_tiers is None:
            include_tiers = [PermissionTier.T0, PermissionTier.T1, PermissionTier.T2]
        return [tool for tool in self._tools.values() if tool.tier in include_tiers]

    def get_tools_for_llm(self) -> List[dict]:
        tools = self.get_available_tools()
        llm_tools = []
        for tool in tools:
            llm_tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters
                }
            })
        return llm_tools

    def can_execute(self, tool_name: str, vehicle_moving: bool = False) -> tuple[bool, str]:
        tool = self.get_tool(tool_name)
        if not tool:
            return False, "Tool not found."
        
        if tool.tier == PermissionTier.T3:
            return False, "Tool is safety critical and cannot be executed."
            
        if vehicle_moving and tool.blocked_while_moving:
            return False, "Tool requires visual attention and is blocked while the vehicle is moving."
            
        return True, "Allowed"

    def requires_confirmation(self, tool_name: str) -> bool:
        tool = self.get_tool(tool_name)
        return tool.requires_confirmation if tool else False

    def log_action(self, tool_name: str, user_id: str, args: dict, result: Any) -> None:
        log_entry = {
            "timestamp": time.time(),
            "tool_name": tool_name,
            "user_id": user_id,
            "args": args,
            "result": result
        }
        self._audit_log.append(log_entry)

    def get_audit_log(self) -> List[Dict[str, Any]]:
        # Return a copy to ensure immutability externally
        return list(self._audit_log)

    def check_red_team_prompt(self, prompt: str) -> bool:
        """
        Check if prompt contains malicious instructions.
        Returns True if prompt is unsafe.
        """
        prompt_lower = prompt.lower()
        red_flags = [
            "turn off brakes",
            "disable brakes",
            "cut brakes",
            "kill brakes",
            "unlock while driving",
            "unlock while moving",
            "disable safety",
            "override safety",
            "ignore safety",
            "bypass safety",
            "force unlock",
            "override tier"
        ]
        for flag in red_flags:
            if flag in prompt_lower:
                return True
                
        # Pattern checks
        if "unlock" in prompt_lower and any(w in prompt_lower for w in ["moving", "driving", "highway", "speed", "fast"]):
            return True
        if "brake" in prompt_lower and any(w in prompt_lower for w in ["turn off", "disable", "cut", "stop", "fail"]):
            return True
        if "safety" in prompt_lower and any(w in prompt_lower for w in ["disable", "override", "ignore", "bypass", "remove"]):
            return True
            
        return False

    def check_safety_violations(self, prompt: str) -> tuple[bool, str]:
        if self.check_red_team_prompt(prompt):
            return True, "Safety critical violation detected: Prompt refused."
        return False, "Prompt passed safety checks."

tool_registry = ToolRegistry()
