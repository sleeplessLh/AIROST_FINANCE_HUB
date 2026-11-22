from flask import request

from database import db
from models import Claim, Event, User

PER_PAGE = 20


# Page number from the URL (defaults to 1).
def page_number():
    page = request.args.get("page", 1, type=int)
    return page if page and page > 0 else 1


# Search text from the URL, trimmed to 100 characters.
def search_text():
    return request.args.get("q", "").strip()[:100]


# Escape LIKE wildcards and wrap the text in %...%.
def like_pattern(text):
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# Apply the search text and status filter to a claim query.
def filter_claims(statement, q, status, allowed_statuses):
    if status in allowed_statuses:
        statement = statement.where(Claim.status == status)

    if q:
        pattern = like_pattern(q)
        conditions = [
            Claim.title.ilike(pattern, escape="\\"),
            Claim.merchant.ilike(pattern, escape="\\"),
            User.name.ilike(pattern, escape="\\"),
            Event.name.ilike(pattern, escape="\\"),
            db.cast(Claim.amount, db.String).ilike(pattern, escape="\\"),
        ]
        claim_number = q.lstrip("#")
        if claim_number.isdigit():
            conditions.append(Claim.id == int(claim_number))

        statement = (
            statement.join(User, Claim.member_id == User.id)
            .join(Event, Claim.event_id == Event.id)
            .where(db.or_(*conditions))
        )

    return statement


# Apply the search text to a user query (AIROST ID, name, email).
def filter_users(statement, q):
    if q:
        pattern = like_pattern(q)
        statement = statement.where(db.or_(
            User.club_id.ilike(pattern, escape="\\"),
            User.name.ilike(pattern, escape="\\"),
            User.email.ilike(pattern, escape="\\"),
        ))
    return statement


# Non-empty filter values to keep in pagination links.
def filter_args(**values):
    return {key: value for key, value in values.items() if value}
