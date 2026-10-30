# server
Backend do projeto Spaços

Requer Python 3.12+ e MongoDB acessível localmente ou via Atlas. O cliente usa a
API assíncrona do PyMongo e compartilha um pool de conexões por execução da API.

## Iniciar o backend com um comando

Na pasta `server`, execute em Bash (Git Bash no Windows):

```bash
bash start.sh
```

O script prepara ou reutiliza `.venv`, instala as dependências se estiverem
faltando, verifica/inicia o MongoDB, aplica o modelo completo e executa os testes
unitários e de integração. A API FastAPI só é iniciada se todas essas etapas
passarem. A documentação fica em `http://127.0.0.1:8000/docs`.

No Windows, o MongoDB precisa estar instalado como serviço `MongoDB`. Se estiver
parado, o script tenta iniciá-lo; essa operação pode exigir abrir Git Bash como
administrador. O serviço continua ativo quando a API é encerrada.
Em Linux/macOS, se nenhuma instância local estiver acessível e `mongod` estiver
no `PATH`, o script inicia uma instância com dados em `.dev/mongodb` e log em
`.dev/mongodb.log`. Somente essa instância criada pelo script é encerrada na saída.
Uma instância já existente é reutilizada e preservada.

Use `Ctrl+C` para encerrar a API. O script inicia somente o backend, sem frontend.
Ele também funciona a partir da raiz do projeto com `bash server/start.sh`.

Para preparar o banco e executar os testes sem iniciar a API:

```bash
bash start.sh --check
```

As configurações podem ser fornecidas por variáveis de ambiente:

```bash
MONGODB_DB_NAME=spacos_test API_PORT=8001 bash start.sh
```

`MONGODB_URI` usa `mongodb://localhost:27017` por padrão. `MONGODB_TEST_URI` usa a
mesma URI, e os testes criam/removem bancos temporários isolados; a conexão de
testes precisa permitir isso. Para uma instância remota indisponível, o script
interrompe a execução sem tentar iniciar um MongoDB local. `PYTHON` permite
selecionar outro executável Python já preparado, em vez da `.venv` local.
O script é para desenvolvimento local e inicia a API sem recarga automática.

## Instalação e configuração

Na pasta `server`, instale as dependências:

```powershell
python -m pip install -e ".[test,lint]"
```

Configure as variáveis de ambiente no terminal antes de iniciar a API ou o script:

```powershell
$env:MONGODB_URI = "mongodb://localhost:27017"
$env:MONGODB_DB_NAME = "spacos"
```

Esses também são os valores padrão quando as variáveis não estão definidas.
Para Atlas ou autenticação, substitua `MONGODB_URI` pela URI da sua instância.
`MONGODB_DB_NAME` seleciona o banco, independentemente do banco indicado na URI.
Arquivos `.env` não são carregados automaticamente pelo script.

## Inicialização do banco

```powershell
python -m app.scripts.init_db
```

O script verifica a conexão e instala o modelo completo originalmente definido em
`database/01_schema_and_indexes.js`: oito coleções, seus validadores `$jsonSchema`
e os 14 índices do projeto (além dos índices `_id_` automáticos).

| Coleção | Validação e índices |
| --- | --- |
| `places` | Local, fontes, filtros e localização geográfica |
| `events` | Evento, fontes, visibilidade e ocorrências |
| `territories` | Território, geometria e busca por tipo/nome |
| `place_curations` | Curadoria e unicidade por local |
| `sync_runs` | Execuções de importação e histórico |
| `users` | Usuário e e-mail único |
| `itineraries` | Roteiro, paradas e busca por usuário/data |
| `user_places` | Favoritos/visitas e unicidade por usuário/local |

Entre os índices estão as duas restrições centrais da API:

| Coleção | Campo | Tipo | Nome |
| --- | --- | --- | --- |
| `places` | `location` | `2dsphere` | `location_2dsphere` |
| `users` | `email` | Único, ascendente | `email_unique` |

Pode ser executado várias vezes. Coleções novas recebem o validador na criação;
as existentes recebem os validadores com `collMod`, sem remover seus documentos
ou índices adicionais. A validação usa `validationLevel: strict` e
`validationAction: error`, como no modelo original. Ela se aplica a inserções e
atualizações futuras; não revalida retroativamente documentos já armazenados.

As definições foram preservadas em `app/core/mongodb_schema.json`, compartilhado
pelo Python e por `database/01_schema_and_indexes.js`. Para alterar campos,
regras ou índices, edite esse JSON e execute novamente a inicialização. O recurso
também é incluído no pacote Python instalado, sem depender da pasta de execução.
O backend não precisa de Node.js nem de mongosh para inicializar o banco.

O script mongosh continua disponível como alternativa, na pasta `server`:

```powershell
mongosh "mongodb://localhost:27017/spacos" --file database/01_schema_and_indexes.js
```

E-mails duplicados, coordenadas inválidas ou índices existentes incompatíveis
fazem a inicialização falhar. Nenhum documento ou índice é removido para corrigir
esses conflitos. A conexão é encerrada também em caso de erro.

## Executar a API

```powershell
python -m uvicorn app.main:app --reload
```

A API também verifica a conexão e garante todas as coleções, validadores e índices
antes de aceitar requisições. Se o banco estiver indisponível ou a preparação do
banco falhar, a
API não conclui a inicialização. A seleção do servidor tem timeout de 5 segundos.
O cliente é fechado ao encerrar a aplicação.

As rotas podem receber o banco com `Depends(get_database)`, importando
`get_database` de `app.core.database`.

## Verificação

```powershell
python -m pytest
python -m ruff check .
```

Os testes usam banco em memória e clientes simulados, sem exigir um MongoDB ativo.
Como o banco em memória não implementa os validadores do MongoDB, há também
testes de integração opcionais contra uma instância real:

```powershell
$env:MONGODB_TEST_URI = "mongodb://localhost:27017"
python -m pytest
```

Esses testes criam bancos temporários `spacos_test_<identificador>`, removidos ao
final. Verificam as oito coleções, todos os validadores e índices, a preservação
de dados em reexecuções, a rejeição de documentos inválidos e e-mails duplicados,
e uma consulta geoespacial real. Sem `MONGODB_TEST_URI`, esses quatro testes são
ignorados e a suíte em memória continua funcionando.
