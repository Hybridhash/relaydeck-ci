"""A mounted editor's revocable ownership token, shared by its render callbacks."""

from dataclasses import dataclass


@dataclass(eq=False, slots=True)
class DraftFieldOwner:
    active: bool = True
    eligible: bool = True
    generation: int = 0

    def invalidate(self) -> None:
        if self.eligible:
            self.generation += 1
        self.eligible = False
