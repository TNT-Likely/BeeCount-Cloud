"""附件墙在真实读/写路由上的分页、引用、权限和缩略图回归。"""
from datetime import datetime, timezone
from io import BytesIO

from PIL import Image
from sqlalchemy import select
from test_mcp_attachments import create, upload

from src.models import AttachmentFile, ReadTxProjection

pytest_plugins = ("test_mcp_attachments",)


def gallery(ctx, **params):
    return ctx.client.get('/api/v1/read/workspace/attachments',
                          params={'ledger_id': ctx.ledger_id, **params}, headers=ctx.headers)


def test_dedup_related_transactions_and_pagination(ledger):
    a = upload(ledger, b'first')
    b = upload(ledger, b'second')
    create(ledger, [a['file_id'], b['file_id']])
    create(ledger, [a['file_id']])
    page = gallery(ledger, limit=1).json()
    assert page['total'] == 2
    assert page['transaction_total'] == 2
    second = gallery(ledger, limit=1, offset=1).json()
    assert page['items'][0]['key'] != second['items'][0]['key']
    both = gallery(ledger).json()['items']
    shared = next(item for item in both if item['key'] == a['file_id'])
    assert len(shared['transactions']) == 2
    assert shared['status'] == 'ready'
    assert len({item['attachment']['originalName'] for item in both}) == 1


def test_date_and_category_must_match_the_same_transaction(ledger):
    file = upload(ledger)
    first = create(ledger, [file['file_id']])
    second = create(ledger, [file['file_id']])
    with ledger.sessions() as db:
        a = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == first['sync_id']))
        b = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == second['sync_id']))
        a.happened_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        a.category_sync_id = 'food'
        b.happened_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
        b.category_sync_id = 'travel'
        db.commit()
    assert gallery(ledger, date_from='2026-02-01T00:00:00Z', category_sync_id='food').json()['total'] == 0
    page = gallery(ledger, date_from='2026-02-01T00:00:00Z', category_sync_id='travel').json()
    assert page['total'] == 1
    assert page['transaction_total'] == 1
    assert len(page['items'][0]['transactions']) == 2
    assert gallery(ledger, date_to='2026-01-01T00:00:00Z').json()['total'] == 0


def test_pending_missing_corrupt_metadata_and_literal_search(ledger):
    tx = create(ledger)
    import json
    with ledger.sessions() as db:
        row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx['sync_id']))
        row.attachments_json = json.dumps([{'fileName': '100%_receipt.jpg'}, {'fileName': 'lost.jpg', 'cloudFileId': 'not-found'}, 'invalid', 42])
        db.commit()
    result = gallery(ledger).json()
    assert result['total'] == 2
    assert {item['status'] for item in result['items']} == {'pending', 'missing'}
    assert gallery(ledger, q='%_').json()['total'] == 1
    with ledger.sessions() as db:
        row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx['sync_id']))
        row.attachments_json = 'broken JSON'
        db.commit()
    assert gallery(ledger).json()['total'] == 0


def test_admin_and_other_users_cannot_list_another_ledger(ledger):
    other, other_ledger, headers = ledger.register('other-gallery@example.com')
    assert gallery(ledger, ledger_id=other_ledger).status_code == 404
    assert ledger.client.get('/api/v1/read/workspace/attachments', params={'ledger_id': ledger.ledger_id}, headers=headers).status_code == 404
    assert ledger.client.get('/api/v1/read/workspace/attachments', params={'ledger_id': ledger.ledger_id}).status_code == 401


def test_thumbnail_auth_size_and_original_integrity(ledger):
    image = BytesIO()
    Image.new('RGB', (1200, 800), '#f3ab32').save(image, format='PNG')
    original = image.getvalue()
    file = upload(ledger, original)
    path = f"/api/v1/attachments/{file['file_id']}/thumbnail"
    first = ledger.client.get(path, headers=ledger.headers)
    assert first.status_code == 200
    assert first.headers['content-type'] == 'image/jpeg'
    with Image.open(BytesIO(first.content)) as thumb:
        assert max(thumb.size) <= 640
    assert ledger.client.get(path, headers=ledger.headers).content == first.content
    assert ledger.client.get(path).status_code == 401
    _, _, headers = ledger.register('thumbnail-stranger@example.com')
    assert ledger.client.get(path, headers=headers).status_code == 403
    assert ledger.client.get(f"/api/v1/attachments/{file['file_id']}", headers=ledger.headers).content == original


def test_unsupported_and_missing_original_thumbnail(ledger):
    file = upload(ledger, b'not a decodable image')
    path = f"/api/v1/attachments/{file['file_id']}/thumbnail"
    assert ledger.client.get(path, headers=ledger.headers).status_code == 415
    with ledger.sessions() as db:
        row = db.get(AttachmentFile, file['file_id'])
        from pathlib import Path
        Path(row.storage_path).unlink()
    assert ledger.client.get(path, headers=ledger.headers).status_code == 404
