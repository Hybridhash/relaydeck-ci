"""Shared synthetic entries for Changes model and scope tests."""

from relaydeck.contracts.git import FileEntry


def entry(file_id: str) -> FileEntry:
    return FileEntry(
        file_id=file_id,
        path="dir/a.txt",
        status="M",
        staged=False,
        unstaged=True,
        generated=False,
        withheld=False,
        rename_limited=False,
        renderable=True,
    )
