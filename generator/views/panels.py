"""View: panels (renders on import; see views/__init__)."""
from config import OUT
from model import (
    ROLES_NOW,
    committee_now,
    experts_reg,
    games,
    groups,
    live_groups,
    scope_words,
    systems,
)
from render import page, tpl


def write_panel(slug, title, body):
    (OUT / slug).mkdir(parents=True, exist_ok=True)
    (OUT / slug / 'index.html').write_text(
        page(title, body, '../', tpl('panels_crumb.html', label=title).strip(),
             seo={'path': slug + '/', 'noindex': True},
             scripts=['page-panels.js']))

def users_with(role):
    return sorted({ev['user'] for (u, r, sc), ev in ROLES_NOW.items() if r == role},
                  key=str.lower)


# ---- expert panel ----
# Everything an expert can do lived behind curl. The powers are real and the
# Principles already grant them, so they belong in the site, in one place, and
# only in front of the people who hold them. The roster is public either way:
# what the panel adds is knowing which of it is yours.
# href is a page that exists or nothing at all: the whole site and a single
# system have no page of their own, so they are named and not linked
panel_scopes = ([{'key': 'site', 'label': 'the whole site', 'href': ''}]
                + [{'key': s, 'label': systems[s]['name'], 'href': ''}
                   for s in sorted(systems)]
                + [{'key': 'group:' + gr['key'], 'label': gr['title'],
                    'href': (f'groups/{gr["key"]}/'
                             if any(l['key'] == gr['key'] for l in live_groups) else '')}
                   for gr in groups]
                + [{'key': k, 'label': g['title'], 'href': f'games/{g["slugpath"]}/'}
                   for k, g in sorted(games.items())])
panel_data = {
    'roster': sorted(({'user': ev['user'], 'scope': ev.get('scope', ''),
                       'label': scope_words(ev.get('scope', '')),
                       'by': ev['by'], 'date': ev['date'],
                       'href': next((s['href'] for s in panel_scopes
                                     if s['key'] == ev.get('scope', '')), '')}
                      for (u, role, scope), ev in ROLES_NOW.items() if role == 'expert'),
                     key=lambda e: (e['user'].lower(), e['scope'])),
    'scopes': panel_scopes,
    'committee': committee_now,
    # games and members are not embedded (#56): the pickers search the
    # archivist as you type; refused and removed games never come back from it
    'groups': [{'key': gr['key'], 'title': gr['title'], 'games': gr.get('games', []),
                } for gr in groups],
    # open removal requests. Only a site-wide expert answers one, so the panel
    # shows them to nobody else, but the request itself is public on the page
    # it is about and in the site log.
}
write_panel('expert', 'Expert panel', tpl('panels_expert.html', panel_data=panel_data))

# ---- founder panel ----
founder_now = sorted({ev['user'].lower() for (u, role, sc), ev in ROLES_NOW.items()
                      if role == 'founder'})
committee_members = users_with('committee')
write_panel('founder', 'Founder', tpl('panels_founder.html', committee_members=committee_members,
                                      founder_now=founder_now))

# ---- steering committee panel ----
# Name claims are answered here rather than on the claim page, because the
# thing that makes them answerable, the requester's email address, must never
# be built into a static page. The list is fetched from the archivist by the
# people entitled to see it, and lives in the browser for as long as they look.
cpanel_data = {
    'committee': committee_now,
    'moderators': users_with('moderator'),
    'editors': users_with('editor'),
    'committeeNames': users_with('committee'),
    'siteExperts': sorted({e['user'].lower() for e in experts_reg if e['scope'] == 'site'}),
    'founders': founder_now,
}
write_panel('committee', 'Steering Committee', tpl('panels_committee.html', cpanel_data=cpanel_data))
