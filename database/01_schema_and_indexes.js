// SPAços v3 — validador MongoDB (mongosh). Executar em um banco novo spacos.
// Campos desconhecidos = null; a API Mapas retorna public=false até em locais descritivamente abertos.
// Fontes são documentos dentro de places/events, curadoria é separada.
const nstr={bsonType:['string','null']}, nb={bsonType:['bool','null']};
const money={bsonType:['double','int','long','decimal','null']};
const timestamp={bsonType:['date','string','null']}; // compatibilidade com NDJSON GeoSampa v2; novos Mapas usam $date
const point={bsonType:'object',required:['type','coordinates'],properties:{
  type:{enum:['Point']},coordinates:{bsonType:'array',minItems:2,maxItems:2,items:{bsonType:['double','int','long','decimal']}}
}};
const source={bsonType:'object',required:['provider','external_id'],properties:{
  provider:{bsonType:'string'},external_id:{bsonType:'string'},layer:nstr,
  updated_at:timestamp,record_url:nstr
}};
const places={bsonType:'object',required:['_id','name','source_keys','sources'],properties:{
  _id:{bsonType:'string'},name:{bsonType:'string',minLength:1},type:nstr,
  categories:{bsonType:'array',items:{bsonType:'string'}},source_category:nstr,
  description:{bsonType:'object',properties:{short:nstr,long:nstr}},
  address:{bsonType:'object',properties:{full:nstr,postal_code:nstr,neighborhood:nstr,municipality:nstr}},
  location:{anyOf:[point,{bsonType:'null'}]},
  territory:{bsonType:'object',properties:{district:nstr,subprefecture:nstr,region:nstr}},
  administration:{bsonType:'object',properties:{level:nstr}},
  contacts:{bsonType:'object',properties:{phone:nstr,website:nstr}},
  // true / false de acesso público só deve ser definido se a semântica for verificada pelo SPAços.
  public_access:nb,source_public_flag:nb,source_status:{bsonType:['int','long','null']},
  publication_status:{enum:['review','published','hidden','archived']},
  opening_hours:{bsonType:'object',properties:{text:nstr,verified:{bsonType:'bool'}}},
  admission:{bsonType:'object',properties:{is_free:nb,price_min:money,price_max:money,price_text:nstr,currency:{bsonType:'string'}}},
  accessibility:{bsonType:'object',properties:{declared:nb,physical_features:{bsonType:'array',items:{bsonType:'string'}},wheelchair:nb,adapted_restroom:nb,notes:nstr}},
  visit_duration_minutes:{bsonType:['int','long','null']},
  source_keys:{bsonType:'array',minItems:1,items:{bsonType:'string'}},
  sources:{bsonType:'array',minItems:1,items:source},
  data_quality:{bsonType:'object',properties:{location_valid:{bsonType:'bool'},location_method:nstr,needs_review:{bsonType:'bool'},detail_level:{enum:['partial','full','curated']},municipality_check:nstr,stale_source:{bsonType:'bool'}}}
}};
const events={bsonType:'object',required:['_id','title','source_keys','sources'],properties:{
  _id:{bsonType:'string'},title:{bsonType:'string',minLength:1},source_type:nstr,
  source_status:{bsonType:['int','long','null']},
  description:{bsonType:'object',properties:{short:nstr,long:nstr}},
  categories:{bsonType:'array',items:{bsonType:'string'}},age_rating:nstr,price_text:nstr,
  sign_language:nb,audio_description:nb,publication_status:{enum:['review','published','hidden','archived']},
  occurrences:{bsonType:'array',items:{bsonType:'object',properties:{
    external_id:nstr,place_id:nstr,source_place_id:nstr,schedule_text:nstr,
    rule:{bsonType:['object','null']},price_text:nstr,starts_at:timestamp,ends_at:timestamp
  }}},
  source_keys:{bsonType:'array',minItems:1,items:{bsonType:'string'}},
  sources:{bsonType:'array',minItems:1,items:source},
  data_quality:{bsonType:'object',properties:{detail_level:{enum:['partial','full','curated']},schedulable:{bsonType:'bool'},schedule_status:{enum:['missing_occurrences','unverified_rule','confirmed']},stale_source:{bsonType:'bool'}}}
}};
const territories={bsonType:'object',required:['_id','kind','name','geometry','sources'],properties:{
  _id:{bsonType:'string'},kind:{enum:['district','subprefecture']},name:{bsonType:'string'},region:nstr,
  geometry:{bsonType:'object',required:['type','coordinates'],properties:{type:{enum:['Polygon','MultiPolygon']},coordinates:{bsonType:'array'}}},
  sources:{bsonType:'array',minItems:1,items:source}
}};
const curations={bsonType:'object',required:['_id','place_id'],properties:{
  _id:{bsonType:'string'},place_id:{bsonType:'string'},pet_friendly:nb,family_friendly:nb,
  lgbtqia_inclusion:nb,hidden_gem:nb,accessibility_notes:nstr,opening_hours_notes:nstr,
  evidence:{bsonType:'array',items:{bsonType:'object'}},reviewed_at:timestamp
}};
const runs={bsonType:'object',required:['_id','provider','started_at','status'],properties:{
  _id:{bsonType:'string'},provider:{bsonType:'string'},started_at:timestamp,finished_at:timestamp,
  status:{enum:['running','success','partial','error']},imported:{bsonType:['int','long']},errors:{bsonType:'array',items:{bsonType:'string'}}
}};
const users={bsonType:'object',required:['_id','email','password_hash'],properties:{
  _id:{bsonType:'string'},name:nstr,email:{bsonType:'string'},password_hash:{bsonType:'string'},
  preferences:{bsonType:'object'},created_at:timestamp
}};
const itineraries={bsonType:'object',required:['_id','user_id','title','stops'],properties:{
  _id:{bsonType:'string'},user_id:{bsonType:'string'},title:{bsonType:'string'},
  date:nstr,preferences:{bsonType:'object'},stops:{bsonType:'array',items:{bsonType:'object',required:['place_id','order'],properties:{
    place_id:{bsonType:'string'},order:{bsonType:'int'},arrival_time:nstr,departure_time:nstr,event_id:nstr
  }}},created_at:timestamp
}};
const userPlaces={bsonType:'object',required:['_id','user_id','place_id','is_favorite','is_visited'],properties:{
  _id:{bsonType:'string'},user_id:{bsonType:'string'},place_id:{bsonType:'string'},
  is_favorite:{bsonType:'bool'},is_visited:{bsonType:'bool'},visited_at:timestamp
}};
function install(name,schema){
 const validator={$jsonSchema:schema};
 if(db.getCollectionNames().includes(name)){
   const r=db.runCommand({collMod:name,validator,validationLevel:'strict',validationAction:'error'});
   if(!r.ok)throw Error(JSON.stringify(r));
 }else{db.createCollection(name,{validator,validationLevel:'strict',validationAction:'error'});}
 print('Coleção: '+name);
}
install('places',places);install('events',events);install('territories',territories);
install('place_curations',curations);install('sync_runs',runs);
install('users',users);install('itineraries',itineraries);install('user_places',userPlaces);
db.places.createIndex({location:'2dsphere'},{name:'location_2dsphere'});
db.places.createIndex({source_keys:1},{unique:true,name:'source_keys_unique'});
db.places.createIndex({publication_status:1,categories:1,'territory.district':1},{name:'map_filter'});
db.places.createIndex({'address.municipality':1},{name:'municipality'});
db.events.createIndex({source_keys:1},{unique:true,name:'event_source_keys_unique'});
db.events.createIndex({publication_status:1,'data_quality.schedulable':1},{name:'event_visibility'});
db.events.createIndex({'occurrences.place_id':1},{name:'occurrences_place'});
db.territories.createIndex({geometry:'2dsphere'},{name:'territories_geospatial'});
db.territories.createIndex({kind:1,name:1},{name:'territory_lookup'});
db.place_curations.createIndex({place_id:1},{unique:true,name:'place_id_unique'});
db.sync_runs.createIndex({provider:1,started_at:-1},{name:'sync_history'});
db.users.createIndex({email:1},{unique:true,name:'email_unique'});
db.itineraries.createIndex({user_id:1,date:1},{name:'user_date'});
db.user_places.createIndex({user_id:1,place_id:1},{unique:true,name:'user_place_unique'});
print('SPAços v3: schema e índices configurados.');
