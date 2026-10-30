// SPAços v3 — inicialização MongoDB (mongosh), no banco selecionado pela conexão.
// Definições compartilhadas com o cliente Python: app/core/mongodb_schema.json.
// Campos desconhecidos = null; a API Mapas retorna public=false até em locais
// descritivamente abertos. Fontes são documentos em places/events; curadoria é separada.
// public_access só deve ser definido se a semântica for verificada pelo SPAços.
// Timestamps aceitam date/string/null para compatibilidade com NDJSON GeoSampa v2.
(() => {
  const fs = require('fs');
  const path = require('path');
  const model = JSON.parse(fs.readFileSync(
    path.join(__dirname, '../app/core/mongodb_schema.json'), 'utf8'
  ));

  for (const [name, definition] of Object.entries(model.collections)) {
    const { indexes, ...options } = definition;
    if (db.getCollectionNames().includes(name)) {
      const result = db.runCommand({ collMod: name, ...options });
      if (!result.ok) throw Error(JSON.stringify(result));
    } else {
      db.createCollection(name, options);
    }
    print('Coleção: ' + name);
  }

  for (const [name, { indexes }] of Object.entries(model.collections)) {
    for (const { key, ...indexOptions } of indexes) {
      db.getCollection(name).createIndex(Object.fromEntries(key), indexOptions);
    }
  }
  print('SPAços v3: schema e índices configurados.');
})();
