# Runbook — les deux tests restants

Préparé le 20/08/2026. Deux tests indépendants, à lancer dans cet ordre : le
premier coûte une heure et informe le papier B, le second occupe une nuit et
conditionne une affirmation du papier A.

---

## Test 1 — `LBBD-part` : partition d'intervalles contre flux unitaire

**Question.** La reformulation de la contrainte 8.15 en flux unitaire est-elle
la cause de l'effondrement du master à l'échelle ? Aujourd'hui le papier B
répond en théorie seule (même polyèdre, transformation unimodulaire, différence
de nombre de non-zéros). Ce run transforme l'argument en mesure.

**Rien à modifier.** Tout existe déjà :

- l'arm `"LBBD-part": Arm("LBBD", ("--lbbd-partition",))` — `campaign.py` l.134
- la comparaison pré-enregistrée `("LBBD-part", "LBBD", "interval partition vs
  unit flow, same solution set")` — `campaign.py`, liste `COMPARISONS`
- le drapeau solveur `--lbbd-partition` et son garde-fou
  `--lbbd-partition-cap` (défaut 20 000 000 non-zéros)

`campaign.csv` a été produit **avant** l'ajout de cet arm : zéro ligne
`LBBD-part`, alors que `LBBD` en a 240.

### Commandes

```bash
cd /path/to/rcpsp_wt_battery
cmake --build build -j
./build/rcpsp_wt_battery --help | grep lbbd-partition   # doit répondre

# ce que ça coûte, sans rien lancer
python3 tests/campaign.py --arms LBBD-part LBBD --csv tests/campaign.csv \
        --resume --dry-run

# le run
python3 tests/campaign.py --arms LBBD-part LBBD --csv tests/campaign.csv \
        --resume --workers 60 --keep-json tests/json_part/
```

`--resume` compare sur une clé à trois champs et saute les 240 runs `LBBD`
déjà présents : seuls les 240 `LBBD-part` s'exécutent. **Coût : ~40 core-h,
soit moins d'une heure sur 60 workers** (`BUDGET_USE["LBBD-part"] = 0.95`,
`--tl` 600 s par défaut).

### Vérification

```bash
python3 tests/verify_solutions.py --from-dir tests/json_part/
python3 tests/campaign.py --report-only tests/campaign.csv
```

Le rapport imprimera la ligne `LBBD-part vs LBBD`. Trois lectures possibles :

- **différence non significative** → les deux formulations se valent en
  pratique, la Proposition sur l'équivalence polyédrale se suffit, et le
  ralentissement vient d'ailleurs ;
- **`LBBD-part` meilleur** → la reformulation en flux *est* le coupable, et
  c'est un résultat à part entière pour B ;
- **`LBBD-part` pire** → le surcoût en non-zéros ($O(h|N|^2)$ contre
  $O(h|N|)$) domine, ce que le texte prédit déjà.

Surveiller les refus par plafond de non-zéros sur les grandes classes : c'est
un résultat, pas une panne, mais il faut le rapporter comme tel.

---

## Test 2 — E1, E3, E4 à `0.1 E_day`

**Question.** E1, E3 et E4 mesurent le stockage à `1.0 E_day`, capacité que E2
montre NPV-négative sur *toutes* les instances. La substitution entre les deux
leviers tient-elle à la capacité qu'une usine achèterait réellement ?

C'est le `\cj{}` l.1706 du papier A, et la première des six perspectives de
recherche que j'ai écrites dans sa conclusion.

### Modifications déjà appliquées

**`config/design.py`** — ajout après `BATTERY_ON_RATIO` :

```python
BATTERY_ON_RATIOS = [BATTERY_ON_RATIO, 0.1]
```

**`bin/02_make_runlist.py`** — trois boucles passent du scalaire à la liste :

| ligne | expérience | avant | après |
|---|---|---|---|
| 187 | E1 | `(0.0, design.BATTERY_ON_RATIO)` | `(0.0, *design.BATTERY_ON_RATIOS)` |
| 204 | E3 | idem | idem |
| 213 | E4 | idem | idem |

**Volontairement inchangés :**

- **ligne 158 (E0)** — validation méthodologique, hors sujet ici, et l'ajout
  d'un niveau coûterait des runs pour rien.
- **lignes 244 et 258 (E6)** — utilisent le scalaire `BATTERY_ON_RATIO` comme
  argument, pas comme boucle. E6 n'est pas concerné par la question et le
  croiser doublerait son coût.

**Pourquoi ajouter et non remplacer.** Le `run_id` contient `f"b{ratio:g}"`
(`02_make_runlist.py` l.109-111). Remplacer 1.0 par 0.1 aurait sorti les
cellules à 1.0 de la runlist et orphelin des dizaines de milliers de runs déjà
calculés. En ajoutant, les identifiants existants restent adressables et l'on
gagne en prime la comparaison 1.0 contre 0.1, qui est l'information réellement
recherchée.

### Commandes

```bash
cd /path/to/rcpsp_wt_battery/experiments
export RCPSP_EXP_DATA=/path/to/data      # si différent de experiments/data

# pas besoin de 01_build_instances.py : aucune instance nouvelle,
# seule la capacité batterie change, et elle est passée en argument -b

python3 bin/02_make_runlist.py           # sonde le binaire réel
```

**Lire `data/budget_report.txt` avant d'aller plus loin.** La ligne
`REMAINING TO RUN` est la seule autorité sur le coût. Attendu : de l'ordre de
**115 000 à 120 000 runs à 60 s, soit ~1 950 core-h ≈ 33 h sur 60 workers**,
contre un plafond `WALL_CLOCK_BUDGET_H = 96`. Vérifier aussi que le rapport
annonce **0 run_id orphelin**.

```bash
# par étapes, du moins cher au plus cher
python3 bin/03_run.py --experiments E1        # ~13 h
python3 bin/03_run.py --experiments E4        # ~4 h
python3 bin/03_run.py --experiments E3        # ~16 h
```

Ne **pas** passer `--rerun-failed` : les nouvelles cellules `b0.1` n'ont pas de
`.meta.json`, donc le driver les exécute, et les runs déjà réussis restent
sautés. `--rerun-failed` ne sert qu'à rejouer des échecs.

### Collecte et analyse

```bash
python3 bin/04_collect.py
# lire integrity_report.txt : taux d'échec et plancher de résolution C5
python3 bin/05_analyse.py --only E1,E3,E4
python3 bin/06_figures.py
```

### Ce qu'il faut regarder

La question n'est pas « les économies sont-elles plus faibles à 0.1 » — elles
le seront forcément. Elle est :

1. **L'indice de substitution tient-il ?** Il vaut 0.25 sous tarif spot à
   `1.0 E_day`. S'il s'effondre à 0.1, l'affirmation « les deux leviers se
   substituent » ne vaut qu'à une capacité que personne n'achète, et il faut
   la restreindre explicitement.
2. **L'effet des prix négatifs survit-il ?** +57.7 points à `1.0 E_day`, et
   d'autant plus fort que le spread est faible. Une petite batterie arbitre
   moins mais capte toujours les heures négatives — l'effet devrait donc
   *mieux* résister que l'arbitrage classique. Si c'est le cas, le résultat
   central de A en sort renforcé.
3. **La frontière service-énergie se déplace-t-elle ?** E4 rapporte ~58 %
   d'économie contre 1.2–1.5 % de dégradation du retard. Le ratio à 0.1 est ce
   qui intéresse un directeur d'usine.

---

## Ce qui n'est dans aucun des deux tests

- **Régénérer `paper_tex/figures/fig_efficiency.pdf`** depuis
  `tests/campaign-1800.csv` : la figure embarquée date du 14/08, la campagne du
  16/08. `python3 experiments/bin/07_figure_efficiency.py tests/campaign-1800.csv`.
  Aucun calcul, quelques minutes.
- **Le chronomètre par appel de sous-problème** (prérequis (iii) du `\cj{}`
  l.2278 de B) : demande d'abord une modification de `SolverLBBD.cpp`, puis un
  nouveau passage de campagne. Non couvert ici.
- **Calibrer E6** : demande des données machine réelles, pas du CPU.
