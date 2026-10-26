import os
import tempfile
from pathlib import Path
import pytest

TEST_DB = os.environ.get('TEST_DATABASE_URL')
if TEST_DB and not TEST_DB.rstrip('/').endswith('huoke_test'):
    raise RuntimeError('Integration tests may only use the dedicated huoke_test database')
os.environ['DATABASE_URL'] = TEST_DB or 'sqlite:///' + str(Path(tempfile.mkdtemp()) / 'test.db')
os.environ['OPERATOR_TOKEN'] = 'test-operator'
os.environ['REVIEWER_TOKEN'] = 'test-reviewer'
os.environ['REAL_SEND_ENABLED'] = 'false'
os.environ['LLM_API_KEY'] = ''

from app.db import Base, engine, init_db, Session, Lead, Contact
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    init_db()
    with Session.begin() as s:
        lead = Lead(source='TEST ONLY', source_key='test:1', url='https://example.test/post',
                    author='Test', title='Need model API', text='Looking for an LLM API provider', fingerprint='test',
                    analysis={'method':'rules','purchase_stage':'unknown'})
        s.add(lead)
        s.flush()
        s.add(Contact(lead_id=lead.id,address='test@example.test',source_url='https://example.test/business',basis='Explicit test-only business contact',verified=True))
    yield


@pytest.fixture
def client():
    with TestClient(app, headers={'Authorization':'Bearer test-reviewer'}) as c:
        yield c


@pytest.fixture
def draft(client):
    response = client.post('/api/leads/1/draft', json={'contact_id':1,'channel':'simulation'})
    assert response.status_code == 200, response.text
    return response.json()


def approve(client, m):
    return client.post(f'/api/messages/{m["id"]}/approve', json={'version':m['version'],'content_hash':m['content_hash']})


def enqueue(client, m):
    assert approve(client, m).status_code == 200
    assert client.post('/api/control/pause',json={'paused':False}).status_code == 200
    r = client.post(f'/api/messages/{m["id"]}/queue',json={})
    assert r.status_code == 200, r.text
    return r.json()
