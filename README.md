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
`app/services/sync_spaces.py` coordena a persistência pelo `PlaceRepository` e o
histórico `sync_runs`.
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

A busca percorre páginas ordenadas por ID e solicita explicitamente os campos
de `SPACE_FIELDS` com `@select`. Na instância SAAS, omitir esse parâmetro retorna
somente `id` (verificado em 03/11/2026), não todos os campos. Usa
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

## Coleta local para testes offline

O comando abaixo coleta a API para arquivos e não grava no MongoDB:

```powershell
python -m app.scripts.export_mapas_spaces --output data/mapas_culturais/sp_2026-11-03
```

Usa a instância SAAS, filtro `status=EQ(1)` e `En_Municipio=ILIKE(São Paulo)`,
500 registros por página e no máximo 20 páginas. `--base-url`, `--page-size`,
`--max-pages` e `--timeout` permitem ajustar esses valores. A execução explicita
a habilitação dessa integração apenas para a coleta; não altera `.env`.

O diretório recebe:

| Arquivo | Uso |
| --- | --- |
| `spaces_raw.json` | Respostas brutas deduplicadas, aceitas pelo job de importação offline |
| `places_normalized.json` | Documentos `places` em Extended JSON, preservando datas BSON |
| `pages/page_*.json` | Páginas brutas já recebidas, preservadas mesmo se a rede falhar depois |
| `manifest.json` | Fonte, data, consulta, contagens, cobertura dos campos, hashes e completude |
| `excluded.json` | Registros descartados pelo normalizador |

A captura de 03/11/2026 foi concluída em cinco páginas de dados: 2.051 registros
únicos, todos normalizáveis, sem exclusões. Desses, 1.879 têm coordenadas
aproveitáveis, 297 informam horário e nenhum informa preço. A data de atualização
mais recente da fonte é 30/07/2024; todos ultrapassam o critério atual de dois anos
e entram com `stale_source: true`. A captura é útil para testes, mas não confirma
o funcionamento atual de cada espaço.

O arquivo foi reproduzido sem rede e importado duas vezes em um MongoDB
temporário: permaneceu com 2.051 documentos, sem duplicatas. A consulta de exemplo
`find_in_bounds(-23.56, -46.73, -23.52, -46.68)` encontrou 101 registros nessa cópia.
O banco temporário foi removido; o banco principal não foi alterado.
Três registros reais representativos estão versionáveis em
`tests/fixtures/mapas_culturais/spaces_sample.json`, com a origem em
`provenance.json`, para regressão offline na CI.

Repetir o comando no mesmo diretório usa os arquivos salvos, verifica o hash e
não faz chamadas à API. Para solicitar uma coleta nova, escolha outro diretório
vazio. Uma captura interrompida, limitada por `--max-pages` ou com contagem
divergente é marcada `complete: false` e o comando termina com código 1.
Os dados parciais continuam disponíveis, mas não representam coleta completa.

Para popular o banco configurado usando somente o arquivo local:

```powershell
python -m app.jobs.sync_places --input data/mapas_culturais/sp_2026-11-03/spaces_raw.json --source-url https://saas.mapas.cultura.gov.br
```

Os documentos entram em `review`. A coleta não publica espaços nem modifica
o banco. Os arquivos completos em `data/mapas_culturais/` ficam fora do Git;
fixtures pequenas em `tests/fixtures/` podem ser versionadas.

Na investigação de 03/11/2026, a consulta nacional mínima respondeu com 55.197
registros na contagem da fonte; o filtro municipal respondeu com 2.051.
Essas contagens não equivalem a um censo de espaços visitáveis: dependem do
preenchimento do município, da publicação e da qualidade da origem.
Também foram observados desafios Cloudflare com HTTP 403 e HTML, enquanto
outras consultas na mesma instância responderam JSON. Esse comportamento é
compatível com proteção de tráfego automatizado; os testes não permitem afirmar
qual regra ou característica da requisição determinou o bloqueio.
O cabeçalho `cf-mitigated: challenge`, quando presente, identifica esse tipo de
[resposta de desafio](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/).
O endereço antigo `mapas.cultura.gov.br/api/...` redirecionou para uma página HTML;
não deve ser assumido como endpoint equivalente da instância SAAS.

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

## Repositório de espaços

`app/repositories/place_repository.py` centraliza o acesso à coleção `places`,
recebendo o banco já conectado e inicializado por `core.database`.

| Método assíncrono | Contrato |
| --- | --- |
| `upsert_many(places)` | Recebe documentos normalizados e retorna a quantidade processada, incluindo atualizações |
| `find_all()` | Retorna todos os documentos, inclusive os sem localização, ordenados por `_id` |
| `find_in_bounds(sw_lat, sw_lng, ne_lat, ne_lng)` | Retorna documentos dentro do polígono delimitado pelos cantos sudoeste e nordeste, ordenados por `_id` |

A gravação reaproveita o schema e os índices existentes, preservando identidade,
publicação, campos curados e outras fontes. Todas as `source_keys` recebidas são
mantidas. Chaves que apontem para locais diferentes exigem revisão manual, sem
fusão automática. O lote é sequencial e não transacional: uma falha preserva as
gravações anteriores. O callback opcional `on_progress` informa o total concluído
para que a sincronização registre corretamente importações parciais.
O job já utiliza esse repositório através do serviço de sincronização.

A consulta por área usa `$geoWithin` com um polígono GeoJSON e o índice
`location_2dsphere`, conforme a
[documentação do MongoDB](https://www.mongodb.com/docs/v8.0/core/indexes/index-types/geospatial/2dsphere/query/geojson-bound-by-polygon/).
Os parâmetros usam latitude/longitude; o polígono interno usa `[longitude, latitude]`.
Os limites precisam ser finitos, estar nos intervalos geográficos e formar uma
área não vazia, sem cruzar o antimeridiano e com menos de 180° de longitude.
Locais sem coordenadas ficam fora da busca por área.

```python
from app.repositories.place_repository import PlaceRepository
from app.schemas.place import PlaceSummarySchema

repository = PlaceRepository(database)
documents = await repository.find_in_bounds(-23.60, -46.70, -23.50, -46.50)
summaries = [PlaceSummarySchema.from_mongo(doc) for doc in documents]
```

`find_all` e `find_in_bounds` retornam documentos de persistência sem aplicar
filtro de publicação. `find_curations(place_ids)` consulta as badges da curadoria
em lote. A camada de serviço/rotas deve
selecionar os documentos visíveis, consultar a curadoria quando necessário e
converter para os schemas de resposta. `find_all()` inclui todos os estados.

`find_in_bounds` atende à área visível de um mapa ou a uma área construída ao
redor da posição do usuário. Não calcula distância, não ordena por proximidade
e não equivale a um raio em quilômetros. Uma futura busca por raio/mais próximos
terá um contrato próprio usando `$near` ou `$geoNear`. A localização do usuário
é um parâmetro de consulta e não precisa ser gravada em `places`.

## Modelos de consulta de espaços

`app/schemas/place.py` define os modelos Pydantic de resposta para o frontend.
O contrato de escrita continua em `app/core/mongodb_schema.json`; esses modelos
projetam documentos persistidos sem modificar o banco ou o pipeline de importação.

| Modelo | Conteúdo |
| --- | --- |
| `GeoPoint` | GeoJSON `Point`, com coordenadas `[longitude, latitude]`, finitas e dentro dos limites geográficos |
| `PlaceSummarySchema` | `id`, `name`, `primary_category`, `location` e `badges` |
| `PlaceDetailSchema` | Resumo mais categorias, descrições, endereço, território, administração, contatos, horário, preço, acessibilidade, duração da visita, fontes, qualidade e curadoria |

Os nomes seguem o backend existente. `_id` é aceito na leitura, mas a resposta
sempre usa `id`, inclusive com a serialização padrão de `response_model` do FastAPI.
A categoria principal é a primeira categoria preenchida em `categories`, com
fallback para `type` e `source_category`. Localização ausente permanece `null`.
Valores BSON `Decimal128` de coordenadas e preços são convertidos para números JSON.

As badges são um objeto com `is_free`, `wheelchair`, `pet_friendly`,
`family_friendly`, `lgbtqia_inclusion` e `hidden_gem`. Cada campo preserva os três
estados: `true` (informação afirmativa), `false` (negativa) e `null` (desconhecida).
O frontend pode exibir um selo quando o valor for `true`; campos ausentes nunca
implicam gratuidade ou inclusão. A declaração genérica de acessibilidade não é
transformada automaticamente em acesso para cadeira de rodas.

```python
from app.schemas.place import PlaceDetailSchema, PlaceSummarySchema

# document vem de places; curation vem de place_curations, quando consultada.
summary = PlaceSummarySchema.from_mongo(document, curation=curation)
detail = PlaceDetailSchema.from_mongo(document, curation=curation)
payload = detail.model_dump(mode="json")
```

Também é possível usar `PlaceSummarySchema.model_validate(document)` diretamente.
`from_mongo` permite fornecer a curadoria separada e verifica seu `place_id`.
Os modelos não consultam o banco. Sem curadoria consultada, suas badges ficam
desconhecidas. Informações desconhecidas de horário e preço continuam nulas.
`source_keys`, flags brutas do provedor, estado de publicação e evidências livres
da curadoria não são incluídos nas respostas. As fontes e avisos de qualidade
úteis para consulta aparecem no detalhe.

Esses modelos não filtram a publicação. O serviço de consulta seleciona os
espaços visíveis e incorpora as badges de `place_curations` antes da resposta.

## API de pinos do mapa

`GET /api/places` retorna um array de `PlaceSummarySchema`, montado pelo serviço
`app/services/place.py` e exposto por `app/api/routers/places.py`.

```text
GET /api/places
GET /api/places?sw_lat=-23.60&sw_lng=-46.70&ne_lat=-23.50&ne_lng=-46.50
```

Sem parâmetros, lista todos os espaços disponíveis para pinos. Com os quatro
limites, consulta a área pelo repositório geográfico. Informar apenas parte dos
limites, coordenadas não finitas ou fora dos intervalos, área vazia/invertida ou
com 180° ou mais de longitude retorna HTTP 422. O contrato não cruza o
antimeridiano e não representa busca por raio ou distância.

A resposta contém `id`, `name`, `primary_category`, `location` e `badges`.
As coordenadas GeoJSON permanecem `[longitude, latitude]`. Só aparecem registros
com `publication_status: published` e localização disponível; registros com
`data_quality.location_valid: false` também ficam de fora. Documentos em revisão,
ocultos, arquivados ou sem estado de publicação não são exibidos.
Como a importação cria registros em `review`, é esperado receber `[]` enquanto
nenhum local tiver sido revisado e publicado. A consulta não publica registros.

As badges de curadoria são consultadas em um único lote para os locais visíveis.
Dados desconhecidos continuam `null`, e campos internos da persistência não são
incluídos. A ordenação é por ID; o endpoint não aplica paginação nem truncamento.
Nenhum resultado produz HTTP 200 com `[]`. Falhas do MongoDB retornam HTTP 503
com uma mensagem genérica. A consulta utiliza apenas o banco local, sem coletar
dados ou chamar o Mapas Culturais durante a requisição.
Os parâmetros e o schema de resposta aparecem em `/docs`.

## Verificação

```powershell
python -m pytest
python -m ruff check .
```

A cobertura mínima da suíte completa continua em 80%. Para executar somente
os testes solicitados, sem aplicar o limite global de cobertura a um recorte:

```powershell
python -m pytest tests/unit/test_place_service.py --no-cov
$env:MONGODB_TEST_URI = "mongodb://localhost:27017"
python -m pytest tests/integration/test_places_api.py --no-cov
```

`tests/unit/test_place_service.py` usa mocks do repositório para validar o
serviço (visibilidade, bbox, badges e falhas) e `httpx.MockTransport` para
verificar a normalização da resposta externa. Os testes anteriores do adaptador,
incluindo paginação, coordenadas inválidas e respostas com erro, continuam em
`tests/test_mapas_culturais.py`.

`tests/integration/test_places_api.py` exercita o endpoint com MongoDB real,
validadores e índice geográfico: listagem, bounding box, ausência de resultados,
visibilidade, curadoria e o fluxo adaptador simulado → sincronização → banco →
resposta pública. Os testes HTTP em memória de validação 422 e falha 503 continuam
em `tests/test_places_api.py`. Testes existentes foram reaproveitados e movidos,
sem manter cópias das mesmas funções em dois lugares.

### CI do backend

O workflow `.github/workflows/ci.yml` pertence ao repositório `server` (submódulo
do projeto principal). Em pushes, pull requests ou execução manual, prepara Python
3.12 e 3.13 e um serviço MongoDB 8.0 temporário, instala `.[test,lint]`, executa
Ruff e roda a suíte em duas etapas:

```bash
python -m pytest -m 'not integration'
python -m pytest -m integration --cov-append
```

O job usa temporariamente `ubuntu-22.04` (kernel 6.8 na imagem consultada em
03/11/2026). O MongoDB 8.0 impede a inicialização nos kernels 6.19 a 7.0.13;
isso faz o serviço falhar antes de qualquer teste. O runner `ubuntu-24.04`
consultado usa kernel 7.0.0 e aciona essa restrição. A troca do runner preserva
a versão do banco e a execução obrigatória da integração. Rever essa escolha
quando o runner mais recente tiver um kernel reconhecido como compatível pelo
MongoDB, observando também o calendário de descontinuação do Ubuntu 22.04.
Referências: [MongoDB](https://www.mongodb.com/docs/manual/release-notes/8.0/),
[runner Ubuntu 22.04](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2204-Readme.md)
e [runner Ubuntu 24.04](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md).

A segunda etapa soma a cobertura de integração à de unitários. As chamadas ao
Mapas Culturais nos testes são simuladas, sem depender da disponibilidade da API
nem de tokens. Não são necessárias credenciais para o MongoDB temporário da CI.
O contêiner é criado por job conforme o
[mecanismo de serviços do GitHub Actions](https://docs.github.com/en/actions/tutorials/use-containerized-services/use-docker-service-containers).
No ambiente `CI=true`, a ausência de `MONGODB_TEST_URI` faz os testes de integração
falharem, impedindo que a esteira passe com essa cobertura ignorada.

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
e consultas geoespaciais reais. A fixture `real_db`, compartilhada em
`tests/conftest.py`, cria e remove um banco exclusivo por teste. Sem
`MONGODB_TEST_URI` no ambiente ou `.env`, os testes de integração são ignorados
apenas na execução local; a suíte em memória continua funcionando.
