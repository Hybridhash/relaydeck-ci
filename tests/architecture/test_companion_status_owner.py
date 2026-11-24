"""Published status readers share the inspection owner."""

from relaydeck.companion.app import lifecycle
from relaydeck.companion.inspection import readiness
from relaydeck.companion.inspection.status import status
from relaydeck.companion.setup.context import SetupContext


def test_status_readers_share_the_inspection_function() -> None:
    assert lifecycle.status is status
    assert readiness.lifecycle_status is status
    assert SetupContext.__dataclass_fields__["companion_state"].default is status
