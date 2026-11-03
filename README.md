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

`MONGO_URI` (ou o alias `MONGODB_URI`) usa `mongodb://localhost:27017` por padrão.
No script, `MONGODB_TEST_URI` usa a mesma URI quando omitido, e os testes criam/removem bancos temporários isolados; a conexão de
testes precisa permitir isso. Para uma instância remota indisponível, o script
interrompe a execução sem tentar iniciar um MongoDB local. `PYTHON` permite
selecionar outro executável Python já preparado, em vez da `.venv` local.
O script é para desenvolvimento local e inicia a API sem recarga automática.

## Instalação e configuração

Na pasta `server`, instale as dependências:

```powershell
python -m pip install -e ".[test,lint]"
```

As configurações são carregadas e validadas com `pydantic-settings`. Não é
necessário criar `.env` para manter o funcionamento local atual. Para configurar:

```powershell
Copy-Item .env.example .env
```

Edite o arquivo `.env` dentro de `server`, que é localizado pelo caminho do
módulo, independentemente da pasta de execução. O arquivo real não é versionado;
`.env.example` documenta as opções sem credenciais. Reinicie a API após alterações.
Variáveis do terminal têm prioridade sobre `.env`; valores omitidos usam os padrões.
O script `start.sh`, a API e os comandos do banco usam a mesma configuração.
O `.env` é interpretado como dados, nunca executado como código de shell.

| Variável | Padrão / regra |
| --- | --- |
| `MONGO_URI` | `mongodb://localhost:27017`; aceita `mongodb://` e `mongodb+srv://` |
| `MONGODB_URI` | Alias de compatibilidade de `MONGO_URI` |
| `MONGODB_DB_NAME` | `spacos`, independentemente do banco indicado na URI |
| `MONGODB_TEST_URI` | Opcional; `start.sh` usa a conexão principal se omitida |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8000`, utilizados por `start.sh` |
| `CORS_ORIGINS` | Origens locais do Vite, separadas por vírgulas |
| `EXTERNAL_API_TIMEOUT_SECONDS` | `10`, deve ser positivo e finito |
| `AUTH_ENABLED` | `false`; quando verdadeiro, exige `JWT_SECRET` |
| `JWT_SECRET` | Sem padrão; quando fornecido, pelo menos 32 caracteres não vazios |
| `MAPAS_CULTURAIS_ENABLED` / `MAPAS_CULTURAIS_BASE_URL` | Desabilitado; ao habilitar, exige URL HTTP(S) |
| `GEOSAMPA_ENABLED` / `GEOSAMPA_BASE_URL` | Desabilitado; ao habilitar, exige URL HTTP(S) |
| `SPTRANS_ENABLED` / `SPTRANS_BASE_URL` / `SPTRANS_TOKEN` | Desabilitado; ao habilitar, exige URL e token |
| `OPEN_METEO_ENABLED` / `OPEN_METEO_BASE_URL` | Desabilitado; URL padrão `https://api.open-meteo.com` |

Se os dois nomes da conexão estiverem definidos na mesma fonte, `MONGO_URI`
prevalece. Uma variável do terminal prevalece sobre o `.env` mesmo quando usa
o alias antigo. Exemplo de configuração pelo terminal:

```powershell
$env:MONGO_URI = "mongodb://localhost:27017"
$env:MONGODB_DB_NAME = "spacos"
```

Use credenciais reais somente no ambiente ou `.env` local. URI do MongoDB, segredo
JWT e token SPTrans usam `SecretStr`, que mascara representações usuais, sem
criptografar o arquivo. Não registre configurações nem seus valores secretos.
Nunca publique esses segredos em variáveis `VITE_*` do frontend.
Para gerar um segredo JWT localmente, pode-se usar
`python -c "import secrets; print(secrets.token_urlsafe(32))"` e guardá-lo no `.env`.

Esses campos preparam a configuração dos futuros módulos; não implementam login,
proteção de rotas ou adaptadores. URLs de Mapas Culturais, GeoSampa e SPTrans
ficam sem padrão até confirmar os endpoints efetivamente usados. Open-Meteo
público não exige token para o uso previsto. Os comandos diretos do banco usam
`DatabaseSettings` e não exigem JWT ou credenciais de integrações.
A validação verifica formatos e pré-requisitos, sem realizar chamadas de rede.
Os adaptadores poderão consultar `get_settings()` ao serem construídos.

## Importar espaços do Mapas Culturais

O adaptador `app/integrations/mapas_culturais.py` oferece `fetch_spaces()`
(também em `MapasCulturaisClient`). Retorna documentos normalizados para `places`;
`app/services/sync_spaces.py` cuida da persistência e do histórico `sync_runs`.
O pipeline é orquestrado por `app/jobs/sync_places.py`: ele consome esses módulos,
abre/fecha a conexão e reutiliza os validadores e índices de
`app/core/mongodb_schema.json`, instalados por `connect_database()`. A API e o
job compartilham esse mesmo schema; não há uma definição paralela de `places`.
Na coleta pela API, os dados já chegam normalizados pelo adaptador. Arquivos
de coleta bruta passam pela mesma função `normalize_space()` antes da gravação.
Não é necessário alterar o modelo do MongoDB. A importação é explícita, fora
do início da API, para não condicionar sua disponibilidade ao provedor externo.

Na pasta `server`, configure a instância desejada e execute:

```powershell
$env:MAPAS_CULTURAIS_ENABLED = "true"
$env:MAPAS_CULTURAIS_BASE_URL = "https://saas.mapas.cultura.gov.br"
python -m app.jobs.sync_places
```

O endereço acima identifica a fonte da amostra anterior do projeto. Na consulta
de 30/10/2026, retornou HTTP 403 (desafio Cloudflare); a disponibilidade da coleta
ao vivo precisa ser confirmada na instância usada. Não há troca silenciosa de fonte.
Consulte o contrato de consulta em
[Documentação da API](https://manual.rededasartes.funarte.gov.br/docs/devs/reference/api/querying).

Para importar uma coleta bruta já existente, identificando sua fonte:

```powershell
python -m app.jobs.sync_places --input "caminho/space_find_detailed.json" --source-url "https://saas.mapas.cultura.gov.br"
```

Essa opção não exige habilitar chamadas externas e registra `source: snapshot` no
histórico; não representa uma coleta atual nem cobertura completa da cidade.

O comando anterior `python -m app.scripts.sync_spaces` permanece compatível e
delega ao mesmo job, aceitando as mesmas opções. Para invocação por outro módulo
assíncrono, use `await sync_places()`, importando a função de
`app.jobs.sync_places`; ela retorna o resumo da execução. O job não é agendado
automaticamente. Uma falha encerra o comando com erro e, após o início da
sincronização, fica registrada em `sync_runs`. Erros de arquivo/configuração
anteriores à conexão não criam histórico no banco.

A busca percorre páginas ordenadas por ID e solicita todos os campos disponíveis
(sem `@select`), permitindo receber metadados opcionais de cada instância. Usa
`status=EQ(1)` e `En_Municipio=ILIKE(São Paulo)`, validando novamente o município
localmente. Na importação de amostras, aceita também o município no final do
endereço quando o campo próprio está ausente. Endereços sem confirmação são
descartados. Coordenadas são GeoJSON `[longitude, latitude]`, finitas e dentro
de uma área de plausibilidade de SP; não substituem a confirmação do município.
Coordenadas ausentes/inválidas ficam nulas, para revisão e fora do mapa.

Nome, categoria da fonte, endereço, horário e preço textual são preservados.
Campos ausentes ficam desconhecidos; não se inferem gratuidade, preço numérico,
acesso público ou horário a partir da descrição. `public` não significa entrada
livre. Todos os novos locais entram como `publication_status: review`.
A categoria da fonte também alimenta `categories`, sem impor taxonomia própria.

Reexecuções atualizam por `source_keys`, mantendo a identidade `mapas_saas:space:ID`
para a instância SAAS e incluindo o hostname nas demais instâncias. Não há
deduplicação automática entre provedores. Outras fontes e o estado de publicação
são preservados. Documentos com `data_quality.detail_level: curated` preservam
seus campos de curadoria; somente a referência da fonte é atualizada.

Falhas de rede, timeout ou formato são traduzidas para as exceções já usadas
pelo backend. A coleta termina antes das gravações: falhas em páginas seguintes
não substituem os dados existentes. Falhas durante a escrita podem deixar uma
importação parcial, registrada como `partial` com a quantidade concluída;
reexecutar permite retomar sem duplicar. Nada é removido por ausência na fonte.
O histórico distingue `running`, `success`, `partial` e `error`.

## CORS: acesso pelo frontend

A API permite por padrão as origens `http://localhost:5173` e
`http://127.0.0.1:5173`, para desenvolvimento com Vite. Para usar outra porta ou
um frontend publicado, configure a lista completa, separada por vírgulas:

```powershell
$env:CORS_ORIGINS = "https://frontend.example.com,http://localhost:5173"
python -m uvicorn app.main:app --reload
```

A variável substitui os padrões. Informe somente a origem (protocolo, endereço e
porta, quando houver), sem caminho nem barra final. Um valor vazio desabilita o
acesso entre origens; `*` é rejeitado para manter as origens explícitas. As
configurações são lidas ao carregar a aplicação: reinicie a API após alterá-las.
O arquivo `server/.env` também pode fornecer `CORS_ORIGINS`.

Os métodos permitidos são `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `OPTIONS` e
`HEAD`. São aceitos os cabeçalhos `Content-Type` e `Authorization`, permitindo
JSON e o futuro JWT enviado como `Authorization: Bearer ...`. O envio de cookies
entre origens não está habilitado. CORS não substitui a autenticação das rotas.

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
testes de integração opcionais contra uma instância real. Os testes unitários isolam as configurações e não consomem
credenciais do `.env` local:

```powershell
$env:MONGODB_TEST_URI = "mongodb://localhost:27017"
python -m pytest
```

Esses testes criam bancos temporários `spacos_test_<identificador>`, removidos ao
final. Verificam as oito coleções, todos os validadores e índices, a preservação
de dados em reexecuções, a rejeição de documentos inválidos e e-mails duplicados,
e uma consulta geoespacial real. Sem `MONGODB_TEST_URI` no ambiente ou `.env`, esses quatro testes são
ignorados e a suíte em memória continua funcionando.
