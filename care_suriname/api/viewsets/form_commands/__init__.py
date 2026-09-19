"""Form command contribution; native CRUD remains on the host."""

from .actions import ActionsMethods
from .artifact_action import ArtifactActionMethods
from .artifact_replay import ArtifactReplayMethods
from .artifact_responses import ArtifactResponsesMethods
from .artifact_source import ArtifactSourceMethods
from .draft import DraftMethods
from .execution import ExecutionMethods
from .mutations import MutationsMethods
from .responses import ResponsesMethods


def command_parts():
    return (
        DraftMethods,
        ActionsMethods,
        ExecutionMethods,
        MutationsMethods,
        ResponsesMethods,
        ArtifactActionMethods,
        ArtifactSourceMethods,
        ArtifactReplayMethods,
        ArtifactResponsesMethods,
    )
