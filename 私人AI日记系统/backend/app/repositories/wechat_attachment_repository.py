"""Stored WeChat attachment ownership lookup."""
from contextlib import closing
from .. import db


def attachment(attachment_id):
    with closing(db.get_conn()) as conn:
        return conn.execute("SELECT * FROM mobile_task_attachments WHERE id=? AND self_id LIKE 'wechat:%'", (attachment_id,)).fetchone()
