"""Media-type helpers shared by every stage.

A "session" is any recording in the archive. Its slug names every derived
file (raw/transcripts/<slug>.json, corpus/<slug>.md, site/<slug>.html), so
adding a media type is just a new entry in TYPES plus manifest rows.
"""

TYPES = {
    "salon": {"label": "Salon", "plural": "Salons"},
    "interview": {"label": "Interview", "plural": "Interviews"},
    "roundtable": {"label": "Roundtable", "plural": "Roundtables"},
    "presentation": {"label": "Presentation", "plural": "Presentations"},
}


def media_type(session):
    return session.get("type") or "salon"


def slug(session):
    return f"{media_type(session)}-{int(session['number']):03d}"


def label(session):
    """'Salon 90', 'Interview 5', ..."""
    return f"{TYPES[media_type(session)]['label']} {int(session['number'])}"


def selected(session, args):
    """CLI selection: no args = everything; 'salon-090' / 'interview-005'
    select by slug; a bare number selects that Salon (the historical usage)."""
    if not args:
        return True
    for a in args:
        if a == slug(session) or (a.isdigit() and media_type(session) == "salon" and int(a) == int(session["number"])):
            return True
    return False
