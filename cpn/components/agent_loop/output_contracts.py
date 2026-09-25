"""HOST-supplied structured output guidance, independent of application roles."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegisteredStructuredOutputContract:
    schema_id: str
    output_port_id: str
    media_type: str
    provider_guidance: tuple[str, ...]

    def __post_init__(self):
        if (not self.schema_id or not self.output_port_id
                or self.media_type != "application/json"
                or any(not isinstance(value, str) or not value.strip()
                       for value in self.provider_guidance)):
            raise ValueError("structured output requires exact schema/port and HOST guidance")
