# Operação local offline

O caminho recomendado é `scripts/smoke_offline.ps1` no Windows ou
`scripts/smoke_offline.sh` no Unix. Eles exigem somente Docker Compose, usam um
projeto/volume próprios e fazem cleanup ao terminar.

Para uma stack local persistente:

```sh
docker compose up --build
curl http://localhost:8000/health
curl http://localhost:8000/ready
docker compose logs api worker-1 worker-2 scheduler
docker compose down
```

O job de migration deve concluir antes da API/workers. Jobs podem ser
inspecionados diretamente na tabela `jobs`; estados terminais são `completed` e
`dead`. Para backup use `scripts/backup_database.sh`; para restore, use
`scripts/restore_database.sh` somente em um banco descartável com
`ALLOW_DESTRUCTIVE_RESTORE=YES`.

Falhas comuns: Docker indisponível exige instalação do Docker Compose; portas
8000/5432 ocupadas exigem liberar a porta ou ajustar a stack persistente; API
não pronta deve ser investigada com `/health`, `/ready` e `docker compose logs`;
falha de pgvector indica imagem/migration incorretos. O smoke offline não
necessita chaves de providers e limpa somente seus próprios recursos.
