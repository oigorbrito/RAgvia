# Quickstart operacional

Este guia usa apenas comandos existentes no repositório. Não há `Makefile`: o Compose e os scripts são as interfaces operacionais canônicas.

## 1. Validação offline do zero

Pré-requisitos: Git, Docker, Docker Compose e daemon Docker ativo. Nenhuma chave de provider é necessária.

Linux/macOS:

```bash
git clone https://github.com/oigorbrito/rpy.git
cd rpy
cp .env.example .env
printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 32)" >> .env
./scripts/smoke_offline.sh
```

Windows PowerShell:

```powershell
git clone https://github.com/oigorbrito/rpy.git
cd rpy
Copy-Item .env.example .env
$pgPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
Add-Content .env "POSTGRES_PASSWORD=$pgPassword"
.\scripts\smoke_offline.ps1
```

O smoke offline reconstrói a imagem, aplica migrations e executa o pipeline com dados sintéticos e zero chamadas pagas. O resultado esperado termina com `RPY OFFLINE SMOKE: PASS`.

## 2. Subir a stack local

Compose exige `POSTGRES_PASSWORD` e publica API/PostgreSQL apenas em `127.0.0.1`; o endereço `postgres:5432` entre containers não muda. Em instalação existente, mantenha em `.env` a senha que já autentica o volume: mudar a variável não troca a senha PostgreSQL do volume e não é feito automaticamente.

```bash
docker compose up -d --build
```

Acompanhar estado e logs:

```bash
docker compose ps
docker compose logs -f worker-1 worker-2
```

Liveness e readiness possuem os aliases públicos alinhados ao contrato v1:

```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
```

`/health` e `/ready` continuam como aliases compatíveis.

A `.env.example` contém somente credenciais locais sintéticas. O bearer de exemplo é `dev-local-token`, associado ao UUID sintético `00000000-0000-0000-0000-000000000151`. Para usar rotas tenant-scoped na stack local, crie esse tenant uma vez:

```bash
docker compose exec -T postgres psql -U rpy -d rpy -c "INSERT INTO tenants (id, name) VALUES ('00000000-0000-0000-0000-000000000151', 'Local quickstart') ON CONFLICT (id) DO NOTHING;"
```

## 3. Tracking Judit

Tracking real é opcional e faz I/O com a Judit nos workers. Antes de criar um monitoramento real, preencha `JUDIT_API_KEY` no `.env` com uma credencial autorizada e recrie os workers:

```bash
docker compose up -d --force-recreate worker-1 worker-2
```

Crie um monitoramento substituindo o CNJ abaixo por um processo que você está autorizado a consultar:

```bash
CNJ='0000000-00.0000.0.00.0000'
curl -X POST \
  -H 'Authorization: Bearer dev-local-token' \
  "http://localhost:8000/v1/trackings/${CNJ}"
```

Listar monitoramentos:

```bash
curl -H 'Authorization: Bearer dev-local-token' \
  http://localhost:8000/v1/trackings
```

Remover um monitoramento usa o `tracking_id` retornado pela API:

```bash
TRACKING_ID='00000000-0000-0000-0000-000000000000'
curl -X DELETE \
  -H 'Authorization: Bearer dev-local-token' \
  "http://localhost:8000/v1/trackings/${TRACKING_ID}"
```

Para uma carteira em lote:

```bash
curl -X POST \
  -H 'Authorization: Bearer dev-local-token' \
  -H 'Content-Type: application/json' \
  -d '{"codes":["0000000-00.0000.0.00.0000","0000000-00.0000.0.00.0001"],"recurrence_days":1}' \
  http://localhost:8000/v1/trackings
```

Não use CNJs reais em testes, fixtures, documentação ou CI. Os exemplos acima são placeholders estruturais, não dados processuais.

## 4. Testes e harnesses

Com Python 3.12 e as dependências de desenvolvimento instaladas:

```bash
python -m pip install pip==26.2.1
python -m pip install --constraint requirements/constraints.txt -e '.[dev]'
python scripts/project_harness.py
pytest -q tests --ignore=tests/integration
```

A integração PostgreSQL requer `TEST_DATABASE_URL` apontando para PostgreSQL 16 + pgvector:

```bash
pytest -q tests/integration
```

A validação provider-free de release continua sendo o smoke offline:

```bash
./scripts/smoke_offline.sh
```

ou, no PowerShell:

```powershell
.\scripts\smoke_offline.ps1
```

## 5. Encerrar a stack local

```bash
docker compose down
```

Use `docker compose down -v` somente quando quiser apagar deliberadamente o volume local do PostgreSQL.
