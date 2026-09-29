from .catalog import pubs_for, load_pubs, archived_sources
from .intake import import_one

def import_batch(dumps, archive, username, today, thumb_base, limit=6,
                 select=None):
    """Import up to `limit` of the SELECTED pending publications.

    The member picks which of their movies come over, one by one; nothing is
    imported that was not asked for by id. A selected co-authored work is
    imported on that selection: ticking it is the member's own act and the
    responsibility for it is theirs, which the import page says in as many
    words. Returns dict with imported ids, skipped reasons, flags, and how
    many of the selection are still pending."""
    wanted = {int(s) for s in (select or [])}
    mine = pubs_for(dumps, username)
    _, subs = load_pubs(dumps)
    existing = archived_sources(archive)
    pending = [p for p in sorted(mine, key=lambda p: p['id'])
               if p['id'] not in existing and p['id'] in wanted]
    imported, skipped, flags = [], [], []
    attempted = 0
    for p in pending:
        if len(imported) >= limit or attempted >= limit * 6:
            break
        attempted += 1
        ok, what, fl = import_one(dumps, archive, p, subs.get(p['submissionId'], {}),
                                  username, today, thumb_base, allow_multi=True)
        flags += fl
        (imported if ok else skipped).append(what)
    return {'imported': imported, 'skipped': skipped, 'flags': flags,
            'remaining': max(0, len(pending) - attempted)}
