from pydantic import BaseModel, ConfigDict


class SetEncounterAdmissionNoteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
