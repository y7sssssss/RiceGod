# Data sources and redistribution status

The MIT license at the repository root covers the project source code and
project-authored documentation. It does **not** grant redistribution rights for
third-party datasets. Each data asset must have independently verified origin
and license information before a public release.

## Current inventory

| Asset | Purpose | Current provenance | Redistribution status |
| --- | --- | --- | --- |
| `data/recipe.json` | Local fallback and Neo4j recipe import; 19,669 recipes in the current evaluation | Original upstream URL, author, collection date, and license are not recorded in this repository | **Unverified. Do not redistribute publicly until provenance is supplied.** |
| `data/data.txt` | Food-culture text split into chunks and indexed in Milvus | Compiled text; per-passage authors, URLs, publication dates, and licenses are not recorded | **Unverified. Local demonstration only. Replace before public deployment.** |
| `data/excipients.json` | Reserved nutrition/ingredient extension; not used by the current core runtime | Original upstream URL and license are not recorded | **Unverified and excluded from the Docker image.** |
| `data/graph_v2.json` | Project-authored aliases, categories, allergen mappings, substitutions, and source metadata | Curated in this project; allergen reference links to the FDA page recorded in the file | Project-authored metadata is covered by the repository license; linked sources retain their own terms |
| `data/evaluation/*.json` | Versioned fixed evaluation inputs | Project-authored test cases | Covered by the repository license, excluding any embedded third-party text |

## Processing performed by RiceGod

1. `scripts.import_recipes_to_neo4j` reads the recipe JSON, normalizes selected
   ingredient aliases, preserves original display text, and creates the Graph V2
   nodes and relationships.
2. `scripts.ingest_food_knowledge_to_milvus` splits `data.txt` with configured
   overlap, embeds each chunk, and stores its filename and chunk index in Milvus.
3. Evaluation scripts compare versioned fixed cases and retain report metadata,
   configuration identity, latency samples, and result hashes.

## Required provenance before a public release

For every externally sourced record or document, record:

- canonical source URL and publisher;
- author or organization;
- retrieval date and content version;
- license or explicit permission;
- transformation and cleaning steps;
- a stable source identifier carried through retrieval into the API response.

Until the missing entries above are resolved, publish the source code without
the unverified data files or replace them with a small, clearly licensed sample
dataset.
