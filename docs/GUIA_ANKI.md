# Guía de Anki

Trucos y respuestas a dudas habituales al estudiar con estos mazos. El itinerario recomendado (los cuatro mazos filtrados) está en el [README](../README.md#itinerario-recomendado-mazos-filtrados).

## Problemas habituales

**El mazo filtrado trae tarjetas de otro tipo** (por ejemplo, tarjetas sin recuadro de dibujo en `Hanzi ← pinyin y significado`). A alguna de sus dos búsquedas le falta el `card:`. Abre las opciones del mazo filtrado y comprueba que los dos filtros llevan el mismo código, por ejemplo `card:PM2H`, y pulsa **Reconstruir**. Las tarjetas que sobran vuelven solas a su mazo.

**«No se encontraron tarjetas coincidentes».**
- Si el filtro es de nuevas (`is:new`), puede que ya hayas introducido todas las palabras del nivel en esa habilidad: cambia el nivel en el filtro 2.
- Comprueba el nombre exacto del mazo y del tipo de tarjeta en el explorador: en la barra lateral, al pulsar un mazo o un tipo de tarjeta, Anki escribe la búsqueda con la sintaxis correcta.
- Si importaste una versión antigua de los mazos, puede que no tenga ese tipo de tarjeta: importa la última versión de [`decks/`](../decks/).
- Las tarjetas suspendidas o que ya están en otro mazo filtrado no aparecen.

**Al importar aparece un tipo de nota con sufijo** (`Chino - HSK (ES/EN)-xxxxx`). Anki no ha podido fusionarlo con el que ya tenías: ver [Actualizar un mazo ya importado](../README.md#actualizar-un-mazo-ya-importado).

## Mazos filtrados: cómo funcionan

- **Las tarjetas no se duplican.** Un mazo filtrado toma prestadas tarjetas de los mazos principales. Lo que respondes en él cuenta igual que en el mazo original (con **Reprogramar tarjetas según mis respuestas** activado), y cuando una tarjeta deja de encajar en la búsqueda vuelve a su mazo con el progreso intacto.
- **Nunca se reconstruye solo.** Se queda como estaba hasta que pulsas **Reconstruir**, y puedes hacerlo cuantas veces quieras al día. En AnkiDroid y AnkiMobile, el botón es la flecha circular de la parte superior.
- **`is:new` no repite tarjetas.** En cuanto respondes una tarjeta nueva, deja de serlo. Cada reconstrucción trae las siguientes, en el orden en que se añadieron (con **Orden añadido**), y si pides más de las que quedan, Anki da las que haya.
- **Las habilidades avanzan sincronizadas.** Todas las tarjetas se crean en el mismo orden de palabras, así que si usas el mismo límite de nuevas en cada mazo filtrado, cada día estudias las mismas palabras en todas las habilidades.

## Cuántas nuevas al día

Cada palabra puede dar diez tarjetas. Si metes N nuevas al día en cada uno de los cuatro mazos del itinerario, introduces 4×N tarjetas nuevas al día, y los repasos crecen durante las primeras semanas hasta estabilizarse. Con 5 al día por habilidad (20 tarjetas), HSK1 (301 palabras) se termina en unos dos meses.

No hace falta el mismo número en todas: en las habilidades que más cuestan (dibujar el hanzi, `PM2H`) conviene meter menos. Para ajustarlo con datos, genera el [informe de progreso](#analizar-tu-progreso) tras una o dos semanas.

## Barajar las tarjetas nuevas

Las palabras de cada nivel están ordenadas más o menos por pinyin, así que en orden de inserción salen seguidas todas las que empiezan por «a». Con el itinerario recomendado no importa (el orden lo decide el filtro 2), pero si estudias desde los mazos principales:

1. Opciones del mazo → **Tarjetas nuevas** → **Orden de recogida** → **Aleatorio**.
2. Para reordenar las que ya tienes: explorador → selecciona las tarjetas nuevas → **Reposicionar** → marca **Barajar**.

## Empezar de cero

Explorador → busca `deck:"Chino - HSK1 (HSK 3.0)"` → selecciona todas → **Olvidar**. Las tarjetas vuelven a ser nuevas y se borra su historial de repasos. Una vez sincronizado no se puede deshacer, así que antes exporta una copia de seguridad (`Archivo > Exportar`, incluyendo la programación).

## Analizar tu progreso

`zhongwen-anki-export-stats` lee el historial de repasos de tu colección y lo guarda en CSV; `zhongwen-anki-analyze-stats` lo convierte en un informe HTML. La instalación está en [Generar o ampliar los mazos](../README.md#generar-o-ampliar-los-mazos).

```bash
zhongwen-anki-export-stats --vocab-dir data --card-snapshot
zhongwen-anki-analyze-stats --cards docs/local/estadisticas/anki_reviews.cards.csv
```

Opciones de la exportación:

| Opción | Qué hace |
|---|---|
| `--collection RUTA` | Colección a leer (`collection.anki2` o `.colpkg`). Si la omites, busca los perfiles de Anki Desktop; si hay varios, te pide que elijas. |
| `--deck TEXTO` | Solo los mazos cuyo nombre contiene ese texto. |
| `--vocab-dir data` | Añade al CSV el pinyin, el significado y el nivel de cada palabra. Hace falta para el análisis de tonos. |
| `--card-snapshot` | Exporta también el estado actual de cada tarjeta (`<salida>.cards.csv`), que el informe usa con `--cards`. |
| `--since AAAA-MM-DD` | Solo los repasos desde esa fecha. |
| `--append` | Añade al CSV existente solo los repasos nuevos. |
| `--list-templates` | Muestra cómo se clasifica cada plantilla de la colección y sale. |

Anki bloquea el perfil que tiene abierto: cierra Anki, o cambia a otro perfil, antes de exportar.

### Si estudias en el móvil

Los comandos leen la colección de Anki Desktop, no la del móvil. Hay dos formas de llevar ahí los repasos de AnkiDroid:

1. **Sincronizando:** sincroniza en AnkiDroid, después en Anki Desktop, y exporta.
2. **Exportando un `.colpkg`:** en AnkiDroid, lista de mazos → menú ⋮ → **Exportar colección** → *Paquete de colección Anki (.colpkg)*. Pásalo al ordenador y usa `--collection mi_coleccion.colpkg`. También sirven las copias automáticas de Anki Desktop (`%APPDATA%\Anki2\<perfil>\backups\*.colpkg` en Windows).

Los repasos no se borran nunca (salvo con **Olvidar**), así que puedes repetir la exportación completa cuando quieras.
