# Comprendre ton système de synchro d'agenda

## Partie 1 — Comment ça marchait avec Zimbra l'année dernière

Il n'y avait pas de magie particulière : **le format ICS (iCalendar) est un standard**, et Google Agenda sait le lire nativement. Le lien HTML que ton ancien prof partageait servait uniquement à *visualiser* l'agenda dans un navigateur — mais Zimbra exposait aussi, à côté, une version `.ics` du même calendrier, à une URL très proche.

Concrètement, un serveur Zimbra qui partage un calendrier publiquement expose en général deux vues du même contenu :

| URL | Format | Usage |
|---|---|---|
| `.../G1.html?view=week` | Page HTML | Consultation humaine dans un navigateur |
| `.../G1.ics` | Texte brut au format iCalendar | Machine-readable, fait pour être *importé* |

Le fichier `.ics` est juste un fichier texte structuré, qui ressemble à ce que produit notre script :

```
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:abc123@zimbra.univ-lille.fr
DTSTART:20260916T063000Z
DTEND:20260916T080000Z
SUMMARY:CM RSX1
END:VEVENT
END:VCALENDAR
```

Quand tu fais **Google Agenda → Autres agendas → À partir de l'URL**, tu ne donnes pas un site web à Google : tu lui donnes l'URL de ce fichier texte. Google va alors :

1. Faire une requête HTTP GET sur cette URL (comme un navigateur qui charge une page, mais sans l'afficher).
2. Lire le contenu comme du texte brut au format iCalendar (RFC 5545).
3. Créer un événement dans ton agenda pour chaque bloc `BEGIN:VEVENT ... END:VEVENT`.
4. **Répéter cette opération périodiquement** (Google ne documente pas l'intervalle exact, mais c'est de l'ordre de 12 à 24h) — c'est ça, la "synchronisation" : Google revient chercher le fichier de temps en temps, et met à jour les événements qui ont changé.

Donc : ce n'était pas une lecture du code source de la page HTML par Google — Google ignorait complètement le HTML. Il consommait directement le fichier `.ics` que Zimbra exposait en parallèle. Notre script fait exactement la même chose : il **fabrique** ce fichier `.ics`, à partir d'une source différente (le JSON du portail au lieu du Zimbra), et GitHub Pages joue le rôle que jouait Zimbra : servir ce fichier à une URL fixe, à laquelle Google vient périodiquement se resservir.

---

## Partie 2 — `generate_ics.py` ligne par ligne

### 2.1 Les imports et les constantes (lignes 23–36)

```python
import sys
import re
import json
import uuid
from datetime import datetime, timezone
from urllib.request import urlopen, Request
```

- `sys` : pour lire les arguments passés en ligne de commande (`sys.argv`).
- `re` : le module d'expressions régulières, pour analyser les titres d'événements.
- `json` : pour parser le fichier JSON téléchargé.
- `uuid` : pour générer un identifiant unique par événement (obligatoire dans le format ICS).
- `datetime`, `timezone` : manipulation des dates.
- `urlopen`, `Request` : pour faire une requête HTTP sans dépendance externe (pas besoin d'installer `requests`).

```python
COURSE_RE = re.compile(r"^(..) (\w+)/")
GROUP_RE = re.compile(r"\(G(\d)\)")
```

Ce sont les traductions directes des deux regex JavaScript du fichier original :
- `COURSE_RE` capture deux groupes : les 2 premiers caractères du titre (`CM`, `TD`, `TP`...) et le mot qui suit avant un `/` (le nom de l'UE, ex: `RSX1`).
- `GROUP_RE` cherche un motif `(G` suivi d'un chiffre `)` quelque part dans le titre, ex: `(G4)`.

`re.compile(...)` **pré-compile** la regex une fois pour toutes (plutôt que de la ré-interpréter à chaque appel), c'est une optimisation standard quand on réutilise la même regex plusieurs centaines de fois.

### 2.2 `fetch_events()` (lignes 39–43)

```python
def fetch_events(url: str) -> list:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (ics-sync-script)"})
    with urlopen(req, timeout=20) as resp:
        data = resp.read().decode("utf-8")
    return json.loads(data)
```

- On construit une requête HTTP avec un en-tête `User-Agent` — certains serveurs refusent les requêtes qui n'ont pas l'air de venir d'un vrai navigateur ou script identifié ; c'est une précaution.
- `urlopen(req, timeout=20)` envoie la requête et attend la réponse, avec un délai maximum de 20 secondes (évite que le script reste bloqué indéfiniment si le serveur ne répond pas).
- Le `with ... as resp:` est un **gestionnaire de contexte** : il garantit que la connexion réseau est bien fermée proprement à la fin, même en cas d'erreur.
- `resp.read()` renvoie des octets bruts (`bytes`), qu'on `.decode("utf-8")` pour obtenir une vraie chaîne de caractères texte.
- `json.loads(data)` transforme ce texte JSON en une structure Python native (ici, une liste de dictionnaires).

### 2.3 `normalize_event()` (lignes 46–63)

C'est la traduction directe de `normalizeEvent()` dans le JS d'origine — je te remets le JS à côté pour comparer :

```javascript
// version JS originale
normalizeEvent(evt) {
    const reCourse = evt.title.match('^(..) (\\w+)/');
    const reGroup = evt.title.match('\(G(\\d)\)');
    if (reCourse == null || !['CM', 'TD', 'TP'].includes(reCourse[1])) {
        evt.color = ...; evt.short = 'special'; evt.nature = 'special'; evt.group = 'G1';
    } else {
        evt.color = ...; evt.short = reCourse[2]; evt.nature = reCourse[1];
        evt.group = reGroup ? reGroup[1] : 'G1';
    }
}
```

```python
def normalize_event(evt: dict) -> dict:
    title = evt.get("title", "")
    course_match = COURSE_RE.match(title)
    group_match = GROUP_RE.search(title)

    if course_match is None or course_match.group(1) not in NATURE_WHITELIST:
        evt["short"] = "special"
        evt["nature"] = "special"
        evt["group"] = None
        evt["is_special"] = True
    else:
        evt["short"] = course_match.group(2)
        evt["nature"] = course_match.group(1)
        evt["group"] = group_match.group(1) if group_match else None
        evt["is_special"] = False

    return evt
```

- `evt.get("title", "")` : récupère la clé `"title"` du dictionnaire, ou une chaîne vide si elle n'existe pas (évite un crash si un événement bizarre n'a pas de titre).
- `COURSE_RE.match(title)` : essaie de faire correspondre la regex **depuis le début** de la chaîne (`match` = ancré au début, contrairement à `search` qui cherche n'importe où). Si ça matche, `course_match.group(1)` est le 1er groupe capturé (`CM`/`TD`/`TP`), `course_match.group(2)` est le 2e (le nom de l'UE).
- `GROUP_RE.search(title)` : cette fois on utilise `search` (pas `match`) car le motif `(G4)` peut être **n'importe où** dans le titre, pas forcément au début.
- La condition `if course_match is None or course_match.group(1) not in NATURE_WHITELIST:` : soit la regex n'a rien trouvé du tout, soit elle a trouvé quelque chose mais ce n'est pas un des 3 codes valides (`CM`, `TD`, `TP`) — par exemple un titre comme `"Férié"` ne matche pas du tout la regex, donc `course_match is None`.
- **J'ai ajouté un champ `is_special`** que le JS n'avait pas explicitement (le JS testait `calendarEvent.short == 'special'` directement) — même logique, formulée différemment, pour plus de clarté en Python.

**Différence importante avec le JS** : en JS, `evt.group = 'G1'` par défaut (pas de `null`). En Python j'ai mis `None` par choix : ça me permet de distinguer clairement "pas de groupe identifié" de "explicitement dans G1", ce qui est plus sûr pour la logique de filtrage juste après. C'est un choix de conception délibéré, pas une erreur de traduction.

### 2.4 `keep_event()` (lignes 66–76) — le filtrage

```python
def keep_event(evt: dict, target_group: str) -> bool:
    if target_group == "All":
        return True
    if evt["is_special"]:
        return True
    if evt["nature"] == "CM":
        return True
    evt_group = f"G{evt['group']}" if evt["group"] else None
    return evt_group == target_group
```

C'est une suite de conditions en cascade, chacune avec un `return` immédiat (dès qu'une condition est vraie, la fonction s'arrête et renvoie `True` — pas besoin d'aller plus loin) :

1. Si on demande `"All"`, tout est gardé, un point c'est tout.
2. Si l'événement est "spécial" (Férié, Réunion, etc.), on le garde toujours, peu importe le groupe — ces événements concernent tout le monde.
3. Si c'est un CM (cours magistral), on le garde aussi toujours — tous les groupes vont au même CM.
4. Sinon (c'est un TD ou TP), on ne le garde que si son groupe correspond exactement au groupe demandé.

La ligne `evt_group = f"G{evt['group']}" if evt["group"] else None` mérite un mot : `evt["group"]` contient juste le chiffre extrait par la regex (ex: `"4"`), donc on reconstruit `"G4"` en le préfixant, pour pouvoir comparer avec `target_group` qui est de la forme `"G4"`.

### 2.5 `parse_iso_utc()` (lignes 79–82)

```python
def parse_iso_utc(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s).astimezone(timezone.utc)
```

Le JSON donne des dates comme `"2026-09-16T06:30:00.000Z"`. Le `Z` final est la notation ISO 8601 pour "UTC" (temps universel). Le problème : les anciennes versions de `datetime.fromisoformat` en Python ne comprenaient pas le `Z` directement (elles veulent `+00:00`), d'où le remplacement. `.astimezone(timezone.utc)` garantit ensuite qu'on manipule bien un datetime "conscient" de son fuseau (un *aware datetime*), ce qui évite des bugs classiques de décalage horaire.

### 2.6 `fold_line()` (lignes 85–95) — une contrainte du format ICS

```python
def fold_line(line: str) -> str:
    if len(line.encode("utf-8")) <= 75:
        return line
    out = []
    while len(line.encode("utf-8")) > 75:
        out.append(line[:74])
        line = " " + line[74:]
    out.append(line)
    return "\r\n".join(out)
```

Le format ICS (RFC 5545) impose que **chaque ligne physique du fichier fasse au maximum 75 octets**. Si une ligne est plus longue (par exemple un titre d'événement très long), il faut la couper en plusieurs lignes, chaque ligne de continuation commençant par un espace. C'est ce qu'on appelle le "line folding". Sans ça, certains clients de calendrier (dont parfois Google) peuvent mal interpréter ou tronquer les lignes trop longues.

`line.encode("utf-8")` convertit la chaîne en octets pour mesurer sa vraie taille en mémoire (un caractère accentué peut faire plus d'un octet), c'est plus rigoureux que `len(line)` qui compte les caractères et non les octets.

### 2.7 `escape_text()` (lignes 98–105)

```python
def escape_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .strip()
    )
```

Dans le format ICS, certains caractères ont une signification spéciale (`;` sépare des paramètres, `,` sépare des valeurs multiples, `\` est le caractère d'échappement lui-même). Si un titre de cours contient un point-virgule ou une virgule, il faut les "échapper" avec un `\` devant, sinon le fichier ICS serait mal formé. On échappe le `\` en premier (sinon on échapperait aussi les `\` qu'on vient d'ajouter pour les autres caractères — l'ordre compte ici).

### 2.8 `build_ics()` (lignes 108–152) — la construction du fichier final

C'est la fonction la plus longue, qui assemble tout :

```python
lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//L3-Info-FIL//Agenda Sync//FR",
    "CALSCALE:GREGORIAN",
    f"X-WR-CALNAME:{escape_text(calendar_name)}",
    "X-WR-TIMEZONE:UTC",
]
```

Ce sont les **en-têtes obligatoires ou conventionnels** d'un fichier ICS :
- `BEGIN:VCALENDAR` / `END:VCALENDAR` : délimitent le calendrier entier.
- `VERSION:2.0` : version du format iCalendar.
- `PRODID` : identifie le logiciel qui a produit le fichier (obligatoire par la RFC, la valeur exacte n'a pas d'importance fonctionnelle).
- `X-WR-CALNAME` : le nom affiché du calendrier dans Google Agenda (propriété "non-standard" mais largement supportée, d'où le préfixe `X-`).

Ensuite, pour chaque événement retenu, on ajoute un bloc :

```python
lines.append("BEGIN:VEVENT")
lines.append(fold_line(f"UID:{uid}@l3-fil-univ-lille"))
lines.append(f"DTSTAMP:{now_stamp}")
lines.append(f"DTSTART:{dtstart}")
lines.append(f"DTEND:{dtend}")
lines.append(fold_line(f"SUMMARY:{escape_text(evt.get('title', ''))}"))
lines.append("END:VEVENT")
```

- `UID` : identifiant **unique et stable** de l'événement — c'est ce qui permet à Google de savoir, d'une régénération à l'autre, "c'est le même événement, juste peut-être modifié" plutôt que de le dupliquer à chaque synchro. C'est pour ça qu'on le calcule (ligne 138-139) à partir du contenu de l'événement (date de début, de fin, titre) plutôt qu'au hasard :
  ```python
  uid_source = f"{start_raw}-{end_raw}-{evt.get('title','')}"
  uid = uuid.uuid5(uuid.NAMESPACE_URL, uid_source)
  ```
  `uuid.uuid5` génère un UUID **déterministe** : les mêmes données en entrée donnent toujours le même UUID en sortie (contrairement à `uuid.uuid4` qui est aléatoire). C'est essentiel ici : si on régénère le fichier dans une heure avec les mêmes données, on veut le même UID pour ne pas créer un doublon dans Google Agenda.
- `DTSTAMP` : la date de création/modification de cette entrée ICS (pas la date de l'événement lui-même) — champ obligatoire par la RFC.
- `DTSTART` / `DTEND` : début et fin de l'événement, au format `AAAAMMJJTHHMMSSZ`.
- `SUMMARY` : le titre affiché de l'événement.

### 2.9 `main()` (lignes 155–177) — le chef d'orchestre

```python
def main():
    if len(sys.argv) < 3:
        print("Usage: ...")
        sys.exit(1)

    target_group = sys.argv[1]
    output_path = sys.argv[2]
```

`sys.argv` est la liste des arguments passés en ligne de commande. `sys.argv[0]` est toujours le nom du script lui-même, donc `sys.argv[1]` et `sys.argv[2]` sont tes deux arguments (`G4` et `output.ics` dans `python3 generate_ics.py G4 output.ics`). Si tu en donnes moins de deux, on affiche l'usage et on quitte avec un code d'erreur (`sys.exit(1)` — le `1` signifie "erreur" pour le système, `0` signifierait "tout va bien").

Le reste enchaîne simplement les fonctions qu'on vient de voir : télécharger → normaliser chaque événement → filtrer → construire le texte ICS → l'écrire dans un fichier.

---

## Partie 3 — Le fichier `update.yml` et GitHub Actions

### 3.1 C'est quoi, YAML ?

YAML (`.yml` ou `.yaml`) est un langage de **description de données structurées**, pensé pour être lisible par un humain (contrairement au JSON qui est plus pensé pour les machines). Les règles essentielles :

- L'**indentation** (les espaces au début de ligne) définit la hiérarchie — un peu comme en Python. Il ne faut jamais mélanger tabulations et espaces.
- `clé: valeur` définit une paire clé-valeur.
- `-` en début de ligne définit un élément de liste.
- Les blocs indentés sous une clé sont ses "enfants" (comme un dictionnaire imbriqué).

### 3.2 Anatomie du fichier `update.yml`

```yaml
name: Update ICS calendar
```
Le nom du workflow, affiché dans l'onglet **Actions** de GitHub.

```yaml
on:
  schedule:
    - cron: '15 * * * *'
  workflow_dispatch: {}
```
La section `on:` définit **quand** ce workflow se déclenche :
- `schedule` avec une expression **cron** : c'est une syntaxe standard Unix pour exprimer une récurrence temporelle, avec 5 champs séparés par des espaces : `minute heure jour-du-mois mois jour-de-semaine`. Un `*` signifie "à chaque valeur possible". Donc `'15 * * * *'` se lit : "à la 15e minute de chaque heure, tous les jours, tous les mois" → autrement dit, toutes les heures à HH:15.
- `workflow_dispatch: {}` : autorise un déclenchement **manuel** depuis l'interface GitHub (le bouton "Run workflow" que tu as utilisé).

```yaml
permissions:
  contents: write
```
Par défaut, un workflow GitHub Actions n'a le droit que de *lire* le contenu du dépôt. Comme notre job doit ensuite faire un `git push` pour déposer le fichier `.ics` généré, on doit explicitement lui donner le droit d'écriture (`write`) sur le contenu du dépôt (`contents`).

```yaml
jobs:
  update-ics:
    runs-on: ubuntu-latest
```
Un workflow est composé d'un ou plusieurs **jobs** (ici un seul, nommé `update-ics`, ce nom est libre). `runs-on: ubuntu-latest` indique sur quel type de machine virtuelle GitHub doit exécuter ce job — ici une machine Linux Ubuntu, gérée et jetable, que GitHub provisionne à la demande puis détruit après.

```yaml
steps:
  - name: Checkout repo
    uses: actions/checkout@v4
```
Un job est composé d'une suite de **steps** (étapes), exécutées dans l'ordre. `uses: actions/checkout@v4` signifie : utilise une **action réutilisable** déjà écrite par quelqu'un d'autre (ici, l'équipe GitHub elle-même), plutôt que d'écrire le code toi-même. `actions/checkout` fait une seule chose : cloner ton dépôt dans la machine virtuelle, pour que les steps suivantes aient accès à tes fichiers.

```yaml
  - name: Set up Python
    uses: actions/setup-python@v5
    with:
      python-version: '3.12'
```
Une autre action réutilisable, qui installe Python 3.12 sur la machine virtuelle (elle n'est pas installée par défaut, ou pas forcément dans la bonne version). `with:` fournit des paramètres à l'action (ici, la version voulue).

```yaml
  - name: Generate ICS file
    env:
      TARGET_GROUP: G4
      OUTPUT_FILENAME: agenda-....ics
    run: |
      mkdir -p docs
      python3 generate_ics.py "$TARGET_GROUP" "docs/$OUTPUT_FILENAME"
```
- `env:` définit des **variables d'environnement**, disponibles dans cette étape (et seulement celle-ci, sauf si définies plus haut au niveau du job).
- `run: |` exécute directement des commandes shell (bash), comme si tu les tapais toi-même dans un terminal. Le `|` indique que ce qui suit est un bloc de texte multi-ligne.
- `mkdir -p docs` crée le dossier `docs/` s'il n'existe pas déjà (`-p` évite une erreur s'il existe déjà).
- `"$TARGET_GROUP"` : la syntaxe `$NOM` en bash récupère la valeur d'une variable d'environnement — ici, celle qu'on vient de définir juste au-dessus.

```yaml
  - name: Commit and push if changed
    run: |
      git config user.name "github-actions[bot]"
      git config user.email "github-actions[bot]@users.noreply.github.com"
      git add docs/
      git diff --quiet && git diff --staged --quiet || git commit -m "Auto-update ICS calendar"
      git push
```
- `git config user.name/user.email` : Git exige de savoir "qui" fait un commit ; comme c'est une machine automatique et pas toi qui tapes la commande, on doit lui donner une identité (ici, une identité conventionnelle pour les bots GitHub).
- `git add docs/` : prépare (indexe) tous les changements dans le dossier `docs/`.
- La ligne `git diff --quiet && ... || git commit ...` est un peu de bash avancé : elle vérifie s'il y a de vrais changements avant de committer (sinon, si le calendrier n'a pas bougé depuis la dernière exécution, on évite de créer un commit vide inutile). Le `&&` et `||` sont des opérateurs de contrôle de flux shell : "si la première commande réussit ET que la deuxième aussi, ne rien faire ; SINON, exécute le commit".
- `git push` : envoie le commit vers GitHub.

### 3.3 Ce que fait le workflow, en résumé (vue d'ensemble)

1. GitHub programme une machine virtuelle temporaire toutes les heures.
2. Elle télécharge ton code (`checkout`).
3. Elle installe Python.
4. Elle exécute ton script, qui télécharge le JSON du FIL et écrit un `.ics` dans `docs/`.
5. Si ce fichier a changé par rapport à la version précédente, elle le commit et le pousse sur GitHub.
6. La machine virtuelle est ensuite détruite (elle ne persiste pas d'une exécution à l'autre — c'est pour ça qu'on doit tout refaire à chaque fois, du clonage à l'installation de Python).

---

## Partie 4 — GitHub Pages

GitHub Pages est un service **d'hébergement de site web statique**, gratuit, directement branché sur un dépôt GitHub. "Statique" veut dire : pas de base de données, pas de code qui s'exécute côté serveur à la demande — juste des fichiers (HTML, CSS, JS, ou dans notre cas, un simple fichier texte `.ics`) servis tels quels à qui les demande.

Le point clé que tu as découvert avec l'erreur 404 : quand tu configures la source comme **"branche `main`, dossier `/docs`"**, GitHub Pages traite ce dossier `docs/` comme la **racine web** du site. Un fichier à `docs/agenda-xxx.ics` dans ton dépôt devient donc accessible à `https://ton-pseudo.github.io/ton-repo/agenda-xxx.ics` — sans le `docs/` dans l'URL, puisque ce dossier *devient* la racine, il ne fait pas partie du chemin visible.

C'est un peu comme un serveur web classique : le dossier que tu pointes comme "racine" du site n'apparaît jamais dans les URLs, seul son *contenu* est visible, à partir de `/`.

---

Si un point reste flou une fois que tu as lu tout ça, n'hésite pas à me demander de zoomer sur un passage précis — on peut aussi faire des petits exercices (par exemple : je te donne un titre d'événement fictif, tu me dis à la main ce que `normalize_event` et `keep_event` vont en faire) si tu veux vérifier que c'est bien assimilé.
