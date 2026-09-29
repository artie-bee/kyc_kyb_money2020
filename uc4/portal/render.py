"""
The customer portal, as HTML. Plain strings, no template engine - the same way
web/render.py builds the console.

Everything here is built from app/customer_view.py, which is the one place that
decides what an applicant may be told. This module lays it out and adds nothing
of its own about the case: no status names, no bands, no scores, no holds, no
findings, because it is never given any.

Every page this returns is scanned again by the server before it is sent
(customer_view.leaks on the whole page), so a leak introduced here is refused
at the door rather than shown.
"""

import html

from app.customer_view import snippet

BANNER = "Demo portal - do not upload real documents."

NAV = [("My application", "/"), ("Documents needed", "/checklist"),
       ("Messages", "/messages")]

STATE_WORD = {"done": "Done", "current": "In progress", "todo": "Not started",
              "skipped": "Not completed"}
# The four statuses customer_checklist() returns, and the colour of each.
STATUS_TONE = {"Accepted": "ok", "Under review": "info", "Resubmission needed": "warn",
               "Not uploaded yet": "neutral"}


def e(value) -> str:
    return html.escape("" if value is None else str(value))


def note(body, tone="info", title="") -> str:
    head = '<span class="note__title">' + e(title) + "</span>" if title else ""
    return '<div class="note note--' + tone + '">' + head + body + "</div>"


def chip(value) -> str:
    return ('<span class="chip chip--' + STATUS_TONE.get(value, "neutral") + '">'
            '<span class="chip__dot"></span>' + e(value) + "</span>")


# ---------------------------------------------------------------------------
# Chrome
# ---------------------------------------------------------------------------

def next_step_card(step, cl) -> str:
    """The single most useful thing to do now, at the top of the page. Built
    from customer_view.next_step(), which reads the checklist as it stands."""
    if step is None:
        return ""
    body = '<p class="nextstep__text">' + e(step["text"]) + "</p>"
    item = step.get("item")
    if step["kind"] == "upload" and item is not None:
        if item["reason"]:
            body += '<p class="nextstep__reason">' + e(item["reason"]) + "</p>"
        body += _upload_area(item, cl, prefix="next-file-")
    tone = "upload" if step["kind"] == "upload" else "quiet"
    return ('<section class="nextstep nextstep--' + tone + '" aria-label="Next step">'
            '<span class="nextstep__label">Next step</span>' + body + "</section>")


def _when(stamp) -> str:
    """2026-09-29T07:40:34Z -> 29 Sep 2026, 07:40 UTC."""
    if not stamp:
        return ""
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    y, m, d = stamp[:10].split("-")
    return f"{int(d)} {months[int(m) - 1]} {y}, {stamp[11:16]} UTC"


def _badge(n) -> str:
    """The unread count beside Messages. Always in the page (so the script can
    update it); hidden when there is nothing unread."""
    return ('<span class="pnav__badge" data-unread="' + str(n) + '"'
            + ("" if n else " hidden") + ' aria-label="' + str(n) + ' unread">'
            + str(n) + "</span>")


def toast(view) -> str:
    """A card announcing the newest unread message, with a link to read it.
    Shown on every page but Messages while anything is unread; the script can
    dismiss it, and shows a new one when a message arrives while the page is
    open. Its words are the message's own (customer_view.snippet)."""
    unread = [m for m in view["messages"] if m["unread"]]
    if not unread:
        return ""
    newest = unread[-1]
    more = (" and " + str(len(unread) - 1) + " more") if len(unread) > 1 else ""
    return ('<aside class="toast" role="status" aria-live="polite" data-toast="'
            + e(newest["communication_id"]) + '">'
            '<span class="toast__dot" aria-hidden="true"></span>'
            '<div class="toast__body"><strong class="toast__title">New message from Wallester'
            + e(more) + "</strong>"
            '<span class="toast__when">' + e(_when(newest["sent_at"])) + "</span>"
            '<p class="toast__text">' + e(snippet(newest["text"], 110)) + "</p>"
            '<a class="btn btn--primary toast__go" href="/messages">Read message</a></div>'
            '<button class="toast__close" type="button" aria-label="Dismiss" hidden>'
            "&times;</button></aside>")


def page(title, body, view=None, active=None, flash=None, demo=False, step=None,
         cl=None, unread=None) -> str:
    """The shell every portal page shares: banner, header, one calm column."""
    nav = ""
    who = ""
    note_new = ""
    if view is not None:
        count = view.get("unread", 0) if unread is None else unread
        links = "".join(
            '<a class="pnav__item' + (" pnav__item--on" if name == active else "")
            + '" href="' + href + '"' + (' aria-current="page"' if name == active else "")
            + ">" + e(name) + (_badge(count) if href == "/messages" else "") + "</a>"
            for name, href in NAV)
        nav = '<nav class="pnav" aria-label="Your application">' + links + "</nav>"
        initials = "".join(w[0] for w in view["applicant_name"].split()[:2]).upper()
        who = ('<div class="phead__who"><span class="phead__avatar" aria-hidden="true">'
               + e(initials) + "</span><span>" + e(view["applicant_name"]) + "</span>"
               '<form method="post" action="/signout">'
               '<button class="btn btn--quiet" type="submit">Sign out</button></form></div>')
        note_new = toast(view) if active != "Messages" else ""
    switch = ('<a class="phead__demo" href="/demo">Switch demo customer</a>'
              if demo else "")

    flash_html = ""
    if flash:
        kind, text = flash
        flash_html = note(e(text), "ok" if kind == "ok" else "warn",
                          "Done" if kind == "ok" else "We could not take that file")

    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>" + e(title) + " &middot; Wallester Business onboarding</title>\n"
        '<link rel="stylesheet" href="/static/common.css">\n'
        '<link rel="stylesheet" href="/static/portal.css">\n</head>\n<body'
        + (' data-poll="1"' if view is not None else "") + ">\n"
        '<div class="demo-banner" role="note">' + e(BANNER) + "</div>\n"
        '<header class="phead"><div class="phead__in">'
        '<a class="phead__brand" href="/"><span class="phead__mark" aria-hidden="true">W</span>'
        'Wallester <span>Business onboarding</span></a>'
        + who + "</div>" + ('<div class="phead__in phead__in--nav">' + nav + switch + "</div>"
                            if nav or switch else "")
        + "</header>\n"
        '<div class="toasts" id="toasts">' + note_new + "</div>\n"
        '<main class="pmain">\n' + flash_html + next_step_card(step, cl) + body
        + "\n</main>\n"
        '<footer class="pfoot"><button class="btn btn--quiet" type="button" id="theme" hidden>'
        "Switch light / dark</button></footer>\n"
        '<script src="/static/portal.js" defer></script>\n</body>\n</html>\n')


def _heading(view, sub=None) -> str:
    return ('<header class="phero"><h1>' + e(view["applicant_name"]) + "</h1>"
            '<p class="sub">Reference ' + e(view["case_id"]) + " &middot; Applied "
            + e(view["applied_on"]) + (" &middot; " + sub if sub else "") + "</p></header>")


# ---------------------------------------------------------------------------
# 1. My application
# ---------------------------------------------------------------------------

def application(view, cl=None) -> str:
    if view["partner"]:
        return (_heading(view) + note(e(view["status_text"]), "info",
                                      "Partner programme")
                + '<p class="lede">You can read our messages to you under '
                  '<a href="/messages">Messages</a>.</p>')

    steps = []
    for s in view["steps"]:
        steps.append(
            '<li class="step step--' + s["state"] + '"'
            + (' aria-current="step"' if s["state"] == "current" else "") + ">"
            '<span class="step__n" aria-hidden="true">' + str(s["n"]) + "</span>"
            '<div class="step__body"><div class="step__head"><span class="step__title">'
            + e(s["title"]) + '</span><span class="step__state">'
            + e(STATE_WORD[s["state"]]) + "</span></div>"
            '<p class="step__line">' + e(s["line"]) + "</p></div></li>")
    action = next((s for s in view["steps"] if s["n"] == 3), None)
    cta = ""
    if action and action["state"] == "current" and not view["closed"]:
        cta = ('<p class="cta"><a class="btn btn--primary" href="/checklist">'
               "Go to your documents</a></p>")
    return (_heading(view)
            + '<div class="overview"><div class="overview__main">'
            + note(e(view["status_text"]), "info")
            + '<section class="card"><h2 class="card__h">Your progress</h2>'
            '<ol class="steps">' + "".join(steps) + "</ol>" + cta + "</section></div>"
            + _side(view, cl) + "</div>")


def _side(view, cl) -> str:
    """The overview's side column: the reference, the latest message and the
    documents - each read from customer_view, nothing added."""
    latest = view["messages"][-1] if view["messages"] else None
    if latest:
        msg = ('<p class="card__meta">' + e(_when(latest["sent_at"]))
               + (' <span class="newtag">New</span>' if latest["unread"] else "") + "</p>"
               '<p class="card__text">' + e(snippet(latest["text"], 120)) + "</p>"
               '<a class="card__link" href="/messages">Open messages</a>')
    else:
        msg = '<p class="card__text muted">We have not sent you any messages yet.</p>'
    docs = ""
    if cl is not None and cl["total_needed"]:
        docs = ('<section class="card"><h2 class="card__h">Documents</h2>'
                '<p class="card__big"><strong>' + str(cl["accepted"]) + "</strong> of "
                + str(cl["total_needed"]) + " accepted</p>"
                '<p class="card__meta">' + str(cl["under_review"]) + " under review &middot; "
                + str(cl["still_needed"]) + " still needed</p>"
                '<a class="card__link" href="/checklist">View documents</a></section>')
    return ('<aside class="overview__side">'
            '<section class="card"><h2 class="card__h">Your reference</h2>'
            '<p class="card__big mono">' + e(view["case_id"]) + "</p>"
            '<p class="card__meta">Quote it if you contact us.</p></section>'
            '<section class="card"><h2 class="card__h">Latest message</h2>' + msg + "</section>"
            + docs + "</aside>")


# ---------------------------------------------------------------------------
# 2. Documents needed
# ---------------------------------------------------------------------------
#
# Everything on this page comes from customer_view.customer_checklist(), read
# fresh for every request. This module decides nothing about what a case
# needs: it names no document type and counts nothing itself.

# A document glyph, for every row. Decorative: the name beside it says it all.
DOC_ICON = ('<svg class="item__icon" viewBox="0 0 20 20" aria-hidden="true" focusable="false">'
            '<path d="M5 2h7l4 4v12H5z" fill="none" stroke="currentColor" stroke-width="1.5"'
            ' stroke-linejoin="round"/><path d="M12 2v4h4" fill="none" stroke="currentColor"'
            ' stroke-width="1.5" stroke-linejoin="round"/></svg>')


def _upload_area(item, cl, prefix="file-") -> str:
    field_id = prefix + item["checklist_item_id"]
    return (
        '<form class="upload" method="post" action="/upload" enctype="multipart/form-data">'
        '<input type="hidden" name="item_id" value="' + e(item["checklist_item_id"]) + '">'
        '<label class="dropzone" for="' + field_id + '">'
        '<span class="dropzone__text">'
        + ("Upload a new copy" if item["status"] == "Resubmission needed" else "Upload a file")
        + '</span><span class="dropzone__hint" hidden>or drag it here</span>'
        '<input class="upload__file" type="file" name="file" id="' + field_id + '" required'
        ' accept="' + e(cl["accept_attr"]) + '"></label>'
        '<button class="btn btn--primary upload__go" type="submit">Send file</button>'
        "</form>")


def _item(item, cl) -> str:
    name = item["document"] + (" - " + item["person"] if item["person"] else "")
    optional = ('<span class="item__optional">optional</span>' if item["optional"] else "")
    reason = ('<p class="item__reason">' + e(item["reason"]) + "</p>"
              if item["reason"] else "")
    previous = ""
    if item["can_upload"] and item["previous_uploads"]:
        previous = ('<p class="item__prev">Previous upload: '
                    '<span class="item__file">' + e(item["previous_uploads"][0]) + "</span>"
                    " (replaced when you upload a new one)</p>")
    form = _upload_area(item, cl) if item["can_upload"] else ""
    # Tips help before an upload; they are guidance, not requirements.
    tips = ('<div class="item__tips">' + "".join('<p class="item__tip">' + e(t) + "</p>"
                                                 for t in item["tips"])
            + "</div>" if item["can_upload"] and item.get("tips") else "")
    tone = {"Accepted": "ok", "Resubmission needed": "warn"}.get(item["status"], "none")
    return ('<li class="item item--' + tone + '" id="item-' + e(item["checklist_item_id"]) + '">'
            '<div class="item__top">' + DOC_ICON
            + '<div class="item__what"><span class="item__doc">' + e(name) + "</span>"
            + optional + "</div>" + chip(item["status"]) + "</div>"
            + reason + previous + tips + form + "</li>")


def checklist(view, cl) -> str:
    types = ", ".join(t.upper() for t in cl["accepted_types"])
    head = ('<header class="phero"><h1>Documents needed</h1>'
            '<p class="sub">' + e(cl["applicant_name"]) + "</p></header>"
            '<div class="needbar"><span class="needbar__count">'
            '<strong>' + str(cl["accepted"]) + "</strong> accepted &middot; "
            '<strong>' + str(cl["under_review"]) + "</strong> under review &middot; "
            '<strong>' + str(cl["still_needed"]) + "</strong> still needed"
            + (" <span class=\"needbar__optional\">(plus " + str(cl["optional"]) + " optional)</span>"
               if cl["optional"] else "") + "</span>"
            '<span class="needbar__types">Accepted files: ' + e(types) + ", up to "
            + str(cl["max_mb"]) + " MB.</span></div>")
    out = [head]
    if cl["partner"]:
        out.append(note(e(view["status_text"]), "info", "Partner programme"))
    if not cl["open"]:
        out.append(note("This application is closed, so we are not taking new documents "
                        "for it.", "info"))
    if cl["items"]:
        out.append('<ul class="items">' + "".join(_item(i, cl) for i in cl["items"]) + "</ul>")
    else:
        out.append('<p class="empty">We will list the documents we need from you here.</p>')
    return "".join(out)


# ---------------------------------------------------------------------------
# 3. Messages
# ---------------------------------------------------------------------------

def messages(view) -> str:
    """An inbox, in the order the messages were sent (the newest last, as in a
    conversation). A message the customer has not opened before is marked New
    on this visit - opening this page is what marks it read."""
    msgs = []
    for m in view["messages"]:
        msgs.append('<li class="msg">'
                    '<div class="msg__head"><span class="msg__from">'
                    '<span class="msg__avatar" aria-hidden="true">W</span>'
                    "Wallester Business onboarding</span>"
                    + ('<span class="newtag">New</span>' if m["unread"] else "")
                    + '<span class="msg__when">' + e(_when(m["sent_at"])) + "</span></div>"
                    '<p class="msg__text">' + e(m["text"]) + "</p></li>")
    unread = sum(1 for m in view["messages"] if m["unread"])
    count = (str(len(msgs)) + " message" + ("" if len(msgs) == 1 else "s")
             + (" &middot; " + str(unread) + " new" if unread else ""))
    body = ('<p class="sub">' + count + '</p><ol class="msgs">' + "".join(msgs) + "</ol>"
            if msgs else '<p class="empty">We have not sent you any messages yet.</p>')
    return _heading(view) + "<h2>Messages we have sent you</h2>" + body


# ---------------------------------------------------------------------------
# 4. Demo case selector
# ---------------------------------------------------------------------------

GROUP_NOTE = {
    "Needs your action": "Documents we are still waiting for.",
    "In review": "Nothing needed from the customer right now.",
    "Closed": "Applications that have finished.",
}
GROUP_TONE = {"Needs your action": "warn", "In review": "info", "Closed": "neutral"}


def _date(stamp) -> str:
    """2026-09-28T11:38:34Z -> 28 Sep 2026."""
    if not stamp:
        return ""
    y, m, d = stamp[:10].split("-")
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return str(int(d)) + " " + months[int(m) - 1] + " " + y


def _card(c) -> str:
    """One customer. Everything on it is customer-safe (customer_view.demo_card)."""
    label = ("Partner programme" if c["partner"] and c["group"] != "Closed" else
             f"{c['still_needed']} document{'s' if c['still_needed'] != 1 else ''} needed"
             if c["group"] == "Needs your action" else
             "With our team" if c["group"] == "In review" else "Closed")
    last = ("Last message " + _date(c["last_message"]) if c["last_message"]
            else "No messages yet")
    search = (c["applicant_name"] + " " + c["case_id"]).lower()
    return (
        '<li class="pick" data-search="' + e(search) + '">'
        '<div class="pick__main">'
        '<div class="pick__top"><span class="pick__name">' + e(c["applicant_name"]) + "</span>"
        '<span class="chip chip--' + GROUP_TONE[c["group"]] + '"><span class="chip__dot">'
        "</span>" + e(label) + "</span></div>"
        '<p class="pick__status">' + e(c["status_text"]) + "</p>"
        '<p class="pick__meta"><span class="pick__ref">' + e(c["case_id"]) + "</span>"
        " &middot; Applied " + e(_date(c["applied_on"])) + " &middot; " + e(last) + "</p>"
        "</div>"
        '<form method="post" action="/demo/open">'
        '<input type="hidden" name="case_id" value="' + e(c["case_id"]) + '">'
        '<button class="btn" type="submit">Open as this customer</button></form></li>')


def demo_selector(customers, q: str = "") -> str:
    """The presenter's front door: start a new application, or open any demo
    customer - grouped by what they need, searchable by name or reference.
    The search is a plain GET form (works with scripts off); portal.js filters
    as you type."""
    q = (q or "").strip()
    shown = [c for c in customers
             if not q or q.lower() in (c["applicant_name"] + " " + c["case_id"]).lower()]
    groups = []
    for group in GROUP_NOTE:
        cards = [c for c in shown if c["group"] == group]
        if not cards:
            continue
        groups.append(
            '<section class="pgroup" data-group>'
            '<h2 class="pgroup__head">' + e(group)
            + ' <span class="pgroup__count" data-count>' + str(len(cards)) + "</span></h2>"
            '<p class="pgroup__note">' + e(GROUP_NOTE[group]) + "</p>"
            '<ul class="picks">' + "".join(_card(c) for c in cards) + "</ul></section>")
    empty = ('<p class="empty" id="pick-empty"' + ("" if not shown else " hidden") + ">"
             "No customer matches that name or reference.</p>")
    clear = (' <a class="psearch__clear" href="/demo">Clear</a>' if q else "")
    return (
        '<header class="phero"><h1>Choose a demo customer</h1>'
        '<p class="sub">Open the portal as any applicant in the demo, without typing '
        "any details. A real customer arrives from the link in our email instead, and "
        "only ever sees their own application.</p></header>"
        '<section class="pstart">'
        '<div><h2 class="pstart__title">New customer</h2>'
        '<p class="pstart__text">Walk through the application form as a new business, '
        "then upload its documents.</p></div>"
        '<a class="btn btn--primary" href="/apply">Start a new demo application</a></section>'
        '<form class="psearch" method="get" action="/demo" role="search">'
        '<label class="psearch__label" for="pick-q">Find a customer</label>'
        '<div class="psearch__row"><input type="search" id="pick-q" name="q" value="' + e(q)
        + '" placeholder="Company name or reference, e.g. WAL-ONB-0002" autocomplete="off">'
        '<button class="btn" type="submit">Search</button>' + clear + "</div></form>"
        + "".join(groups) + empty)


# ---------------------------------------------------------------------------
# Pages with no case
# ---------------------------------------------------------------------------

def signed_out(demo=False) -> str:
    extra = ('<p class="cta"><a class="btn btn--primary" href="/demo">'
             "Choose a demo customer</a></p>" if demo else "")
    return ('<header class="phero"><h1>Your application</h1></header>'
            + note("Open your application from the link in the email we sent you. "
                   "If the link has stopped working, reply to that email and we will send "
                   "you a new one.", "info") + extra)


def not_found() -> str:
    return ('<header class="phero"><h1>Page not found</h1></header>'
            + note('There is nothing at this address. <a href="/">Back to your '
                   "application</a>.", "info"))


def refused() -> str:
    return ('<header class="phero"><h1>This page is not available</h1></header>'
            + note("Something went wrong on our side while preparing this page. Please try "
                   "again later.", "warn"))
