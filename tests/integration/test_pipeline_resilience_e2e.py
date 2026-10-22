from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

import app.rag as rag
from app.claim_evidence import build_material_claims
from app.summary_output import structured_summary_document
import app.process_requests as process_requests
from app.api import app
from app.db import create_pool
from app.judit_client import JuditRequestResult
from app.migrations import migrate
from app.worker import Worker, WorkerSettings


def _prime_fake_structured_summary(context: dict) -> None:
    payload = {
        "synthesis": "Síntese factual de teste.",
        "timeline": [],
        "current_status": "Situação atual registrada nos autos.",
        "attention": ["Nenhuma divergência objetiva identificada."],
        "decisions": [],
        "deadlines": [],
        "related_processes": [],
        "attachments": [],
    }
    payload["claims"] = build_material_claims(
        payload,
        evidence_refs=[str(context["_process_evidence_ref"])],
    )
    context["_parsed_summary"] = payload
    context["_structured_summary"] = structured_summary_document(payload, context)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is required for PostgreSQL integration tests")
FIXTURES = Path(__file__).parents[1] / "fixtures" / "judit"

def _fixture(name: str) -> dict:return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
def _worker(pool) -> Worker:
 assert TEST_DATABASE_URL is not None
 return Worker(pool,WorkerSettings(database_url=TEST_DATABASE_URL,concurrency=1,heartbeat_interval_seconds=60,stale_after_seconds=120,task_timeout_seconds=20,reclaim_interval_seconds=60))
async def _reset(pool) -> None:
 async with pool.acquire() as conn:await conn.execute("TRUNCATE jobs, judit_deliveries, judit_request_completions, tenant_judit_requests, process_summaries, process_steps, tenant_processes, access_log, process_versions, processes, tenants RESTART IDENTITY CASCADE")
async def _grant_tenant(pool,monkeypatch,process_id):
 tenant_id=uuid4(); bearer_token=f"e2e-token-{uuid4()}"; monkeypatch.setenv("RPY_BEARER_TOKENS",json.dumps({bearer_token:str(tenant_id)})); app.state.bearer_tokens={bearer_token:tenant_id}
 async with pool.acquire() as conn:await conn.execute("INSERT INTO tenants (id, name) VALUES ($1, 'e2e tenant')",tenant_id); await conn.execute("INSERT INTO tenant_processes (tenant_id, process_id) VALUES ($1, $2)",tenant_id,process_id)
 return tenant_id,bearer_token

@pytest.mark.asyncio
async def test_requested_cnj_reaches_tenant_scoped_published_summary(monkeypatch:pytest.MonkeyPatch)->None:
 assert TEST_DATABASE_URL is not None; await migrate(TEST_DATABASE_URL); pool=await create_pool(TEST_DATABASE_URL,min_size=2,max_size=6); app.state.pool=pool; monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN","e2e-request-webhook"); monkeypatch.setenv("DATABASE_URL",TEST_DATABASE_URL); monkeypatch.setenv("ANTHROPIC_API_KEY","test-key")
 tenant_id=uuid4(); token=f"request-token-{uuid4()}"; app.state.bearer_tokens={token:tenant_id}; request_id=f"request-{uuid4()}"; response_id=f"response-{uuid4()}"; code=f"0000000-00.2026.8.21.{str(uuid4().int)[-4:]}"; provider_calls=0
 async def fake_request(requested_code:str):
  nonlocal provider_calls; provider_calls+=1; assert requested_code==code; return JuditRequestResult(request_id=request_id)
 async def fake_generate(client,context,validation_errors=None): _prime_fake_structured_summary(context); warning = context.get("source_warnings", [])[0] if context.get("source_warnings") else "Nenhuma divergência objetiva identificada."; return f"# Resumo do processo\n\nProcesso {code}. Situação atual registrada nos autos.\n\n## Pontos de atenção\n{warning}"
 monkeypatch.setattr(process_requests,"create_lawsuit_request",fake_request); monkeypatch.setattr(rag,"_generate",fake_generate)
 lawsuit=deepcopy(_fixture("tracking_lawsuit_response.json")); lawsuit["callback_id"]=f"lawsuit-{uuid4()}"; lawsuit["payload"]["request_id"]=request_id; lawsuit["payload"]["response_id"]=response_id; lawsuit["payload"]["response_data"]["code"]=code
 completion=deepcopy(_fixture("tracking_request_completed.json")); completion["callback_id"]=f"completion-{uuid4()}"; completion["payload"]["request_id"]=request_id
 transport=httpx.ASGITransport(app=app)
 try:
  await _reset(pool)
  async with pool.acquire() as conn:await conn.execute("INSERT INTO tenants(id,name) VALUES($1,'request tenant')",tenant_id)
  worker=_worker(pool)
  async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
   headers={"Authorization":f"Bearer {token}"}; assert (await client.get(f"/processes/{code}",headers=headers)).status_code==404
   requested=await client.post(f"/processes/{code}/request",headers=headers); assert requested.status_code==202; assert requested.json()=={"code":code,"status":"processing","created":True}; assert "request_id" not in requested.json()
   repeated=await client.post(f"/processes/{code}/request",headers=headers); assert repeated.status_code==202; assert repeated.json()["created"] is False; assert provider_calls==0
   assert await worker.process_one() is True; assert provider_calls==1
   assert (await client.post("/webhooks/judit/e2e-request-webhook",json=lawsuit)).status_code==200
   visible=await client.get(f"/processes/{code}",headers=headers); assert visible.status_code==200; assert visible.json()["summary"] is None
   assert (await client.post("/webhooks/judit/e2e-request-webhook",json=completion)).status_code==200
  assert await worker.process_one() is True; assert await worker.process_one() is True; assert await worker.process_one() is False
  async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
   published=await client.get(f"/processes/{code}",headers={"Authorization":f"Bearer {token}"})
  assert published.status_code==200; body=published.json(); assert body["summary_status"]=="available"; assert body["summary"]["validation"]["passed"] is True; assert code in body["summary"]["markdown"]
  async with pool.acquire() as conn:
   assert await conn.fetchval("SELECT count(*) FROM tenant_judit_requests WHERE tenant_id=$1 AND process_code=$2",tenant_id,code)==1
   assert await conn.fetchval("SELECT count(*) FROM tenant_processes tp JOIN processes p ON p.id=tp.process_id WHERE tp.tenant_id=$1 AND p.code=$2",tenant_id,code)==1
   assert await conn.fetchval("SELECT count(*) FROM access_log WHERE tenant_id=$1 AND process_code=$2 AND action='request_process'",tenant_id,code)==2
 finally:await pool.close()

@pytest.mark.asyncio
async def test_completion_before_lawsuit_reaches_authenticated_summary_with_worker_retry(monkeypatch:pytest.MonkeyPatch)->None:
 assert TEST_DATABASE_URL is not None; await migrate(TEST_DATABASE_URL); pool=await create_pool(TEST_DATABASE_URL,min_size=2,max_size=6); app.state.pool=pool; monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN","e2e-resilience-webhook"); monkeypatch.setenv("DATABASE_URL",TEST_DATABASE_URL); monkeypatch.setenv("ANTHROPIC_API_KEY","test-key")
 lawsuit=deepcopy(_fixture("tracking_lawsuit_response.json")); completion=deepcopy(_fixture("tracking_request_completed.json")); request_id=f"request-{uuid4()}"; response_id=f"response-{uuid4()}"; code=f"0000000-00.2026.8.21.{str(uuid4().int)[-4:]}"; lawsuit["callback_id"]=f"lawsuit-{uuid4()}"; lawsuit["payload"]["request_id"]=request_id; lawsuit["payload"]["response_id"]=response_id; lawsuit["payload"]["response_data"]["code"]=code; completion["callback_id"]=f"completion-{uuid4()}"; completion["payload"]["request_id"]=request_id; generation_calls=0
 async def flaky_generate(client,context,validation_errors=None):
  nonlocal generation_calls; generation_calls+=1
  if generation_calls==1:raise RuntimeError("transient provider failure")
  _prime_fake_structured_summary(context)
  warning = context.get("source_warnings", [])[0] if context.get("source_warnings") else "Nenhuma divergência objetiva identificada."
  return f"# Resumo do processo\n\nProcesso {code}. Situação atual registrada nos autos.\n\n## Pontos de atenção\n{warning}"
 monkeypatch.setattr(rag,"_generate",flaky_generate); transport=httpx.ASGITransport(app=app)
 try:
  await _reset(pool)
  async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:assert (await client.post("/webhooks/judit/e2e-resilience-webhook",json=completion)).status_code==200; worker=_worker(pool); assert await worker.process_one() is True; assert (await client.post("/webhooks/judit/e2e-resilience-webhook",json=lawsuit)).status_code==200
  assert await worker.process_one() is True; assert await worker.process_one() is True
  async with pool.acquire() as conn:
   failed=await conn.fetchrow("SELECT id,status::text AS status,attempts FROM jobs WHERE task_name='generate_process_summary' ORDER BY created_at DESC LIMIT 1"); assert failed and failed["status"]=="pending" and int(failed["attempts"])==1; await conn.execute("UPDATE jobs SET run_at=NOW() WHERE id=$1",failed["id"])
  assert await worker.process_one() is True; assert await worker.process_one() is False; assert generation_calls==2
  async with pool.acquire() as conn:process=await conn.fetchrow("SELECT id,current_version_id FROM processes WHERE code=$1",code); assert process; summary=await conn.fetchrow("SELECT markdown,validation FROM process_summaries WHERE process_id=$1 AND version_id=$2",process["id"],process["current_version_id"]); assert summary and summary["validation"]["passed"] is True
  tenant_id,bearer_token=await _grant_tenant(pool,monkeypatch,process["id"])
  async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:response=await client.get(f"/processes/{code}",headers={"Authorization":f"Bearer {bearer_token}"})
  assert response.status_code==200 and response.json()["summary"]["validation"]["passed"] is True
  async with pool.acquire() as conn:assert await conn.fetchval("SELECT count(*) FROM access_log WHERE tenant_id=$1 AND process_code=$2 AND action='read_process_summary'",tenant_id,code)==1
 finally:await pool.close()

@pytest.mark.asyncio
async def test_cached_lawsuit_finalizes_without_summary_generation(monkeypatch:pytest.MonkeyPatch)->None:
 assert TEST_DATABASE_URL is not None; await migrate(TEST_DATABASE_URL); pool=await create_pool(TEST_DATABASE_URL,min_size=2,max_size=6); app.state.pool=pool; monkeypatch.setenv("JUDIT_WEBHOOK_TOKEN","e2e-cached-webhook"); monkeypatch.setenv("DATABASE_URL",TEST_DATABASE_URL)
 lawsuit=deepcopy(_fixture("tracking_lawsuit_response.json")); request_id=f"request-{uuid4()}"; response_id=f"response-{uuid4()}"; code=f"0000000-00.2026.8.21.{str(uuid4().int)[-4:]}"; lawsuit["callback_id"]=f"lawsuit-{uuid4()}"; lawsuit["payload"]["request_id"]=request_id; lawsuit["payload"]["response_id"]=response_id; lawsuit["payload"]["response_data"]["code"]=code; lawsuit["payload"]["tags"]["cached_response"]=True; completion={"callback_id":f"completion-{uuid4()}","event_type":"request_completed","reference_type":"request","reference_id":request_id,"payload":{"status":"completed"}}
 async def provider_must_not_run(*args,**kwargs):raise AssertionError("cached Judit response must not generate a summary")
 monkeypatch.setattr(rag,"_generate",provider_must_not_run); transport=httpx.ASGITransport(app=app)
 try:
  await _reset(pool)
  async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:assert (await client.post("/webhooks/judit/e2e-cached-webhook",json=lawsuit)).status_code==200; assert (await client.post("/webhooks/judit/e2e-cached-webhook",json=completion)).status_code==200
  worker=_worker(pool); assert await worker.process_one() is True; assert await worker.process_one() is False
  async with pool.acquire() as conn:process=await conn.fetchrow("SELECT id,current_version_id FROM processes WHERE code=$1",code); assert process and process["current_version_id"] is not None; assert await conn.fetchval("SELECT count(*) FROM process_summaries WHERE process_id=$1",process["id"])==0; assert await conn.fetchval("SELECT count(*) FROM jobs WHERE task_name='generate_process_summary'")==0
  _,bearer_token=await _grant_tenant(pool,monkeypatch,process["id"])
  async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:response=await client.get(f"/processes/{code}",headers={"Authorization":f"Bearer {bearer_token}"})
  assert response.status_code==200 and response.json()["summary"] is None
 finally:await pool.close()
