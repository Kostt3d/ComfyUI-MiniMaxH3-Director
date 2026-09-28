# MiniMax H3 Promax 0.2

Promax est un Director MiniMax H3 écrit dans ce dépôt pour une cible **RTX 5070 12 Go de VRAM**, avec environ **48 Go de RAM système**. Il ne dérive pas de l'ancien gros nœud Director : son compilateur de plans, son interface et ses nœuds de continuation sont séparés.

Cette branche reste une **alpha** : les tests hors GPU et les vérifications structurelles ne remplacent pas un vrai rendu sur la machine cible. Le premier objectif est donc la stabilité et la cohérence avant de chercher chaque seconde de gain.

## Installation / mise à jour

Dans le dossier du custom node :

```powershell
git fetch origin
git switch feat/minimax-h3-promax
git pull --ff-only
```

Redémarrer ComfyUI puis recharger l'interface.

Pour une nouvelle installation, depuis `ComfyUI/custom_nodes` :

```powershell
git clone --branch feat/minimax-h3-promax https://github.com/Kostt3d/ComfyUI-MiniMaxH3-Director.git
```

Ne pas installer une seconde copie si ce dépôt est déjà présent.

## Nœuds Promax

Menu `MiniMax H3 / Promax` :

- **MiniMax H3 Promax · Director** : scénario, plans, références et conditioning H3.
- **Promax · Last Frame** : dernière image d'un rendu pour une reprise FL2V classique.
- **Promax · Latent Continuation** : vraie continuation AV à partir du latent échantillonné précédent.
- **Promax · Append Continuation** : retire le contexte latent caché et ajoute uniquement la nouvelle portion au latent cumulatif.
- **Promax · Save AV Latent** : sauvegarde vidéo + audio H3 dans un fichier `.promaxlatent`.
- **Promax · Load AV Latent** : recharge ce latent après redémarrage ou dans un autre workflow.
- **Promax · Trim Continuation Media** : après décodage du nouveau window, retire automatiquement les frames/audio de contexte cachées avant export.

Le format `.promaxlatent` existe parce que le latent H3 est un `NestedTensor` contenant deux flux synchronisés, vidéo et audio. Le `SaveLatent` ComfyUI classique n'est pas utilisé pour cette persistance Promax.

## Workflows de base

- **`example_workflows/Promax-12GB-4B-Turbo.json`** : encodeur 4B + projection ClipProj + LoRA Turbo correspondant au modèle. Variante orientée vitesse. Nécessite le nœud externe `ClipProjApply` déjà utilisé dans le workflow utilisateur.
- **`example_workflows/Promax-12GB-Base.json`** : H3 natif, encodeur H3 32B, sans Turbo ni ClipProj. Cette variante exerce une pression importante sur la RAM.
- **`Promax-12GB-Base.api.json`** : version API de la base, pas le fichier UI à glisser dans ComfyUI.

Les noms de poids préremplis sont des exemples correspondant au contexte du projet. Toujours sélectionner les fichiers réellement installés.

## Director : modes normaux

### T2V

Texte vers vidéo/audio. Utilise le chemin FL2VA sans première ni dernière frame.

### FL2V

Première image obligatoire, dernière image facultative. La dernière frame d'un clip précédent peut servir de `first_frame`, mais cela reste une **reprise par image**, pas une continuation latente.

### Ref2V

Références image/vidéo/audio. Les images sont numérotées depuis `reference_1` sans trou. `summary` et les champs `retained` sont compilés dans le prompt Ref2V.

`retained` est une consigne de rétention donnée au modèle, pas un masque ni une garantie mathématique de reproduction parfaite.

## Formats et durée

- `480×864` : preset équilibré utilisé dans le projet ; rapport exact 5:9.
- `576×1024` : 9:16 exact.
- `288×512` : 9:16 exact, utile pour un test léger.
- Les formats paysage inversent ces dimensions.
- Dimensions divisibles par 32.
- Un clip Director normal demande 4 à 15 secondes.
- H3 cale les frames sur sa grille `17k+5` à 24 fps.

Exemples :

- 5 s demandées → 124 frames → 5,167 s réelles.
- 15 s demandées → 362 frames → 15,083 s réelles.

Les timecodes restent des consignes de storyboard. Ils ne sont pas des cuts garantis frame par frame.

## Vraie continuation latente Promax

La continuation 0.2 n'utilise pas la dernière image comme unique mémoire. Elle utilise directement la **queue synchronisée du latent vidéo + audio déjà échantillonné** comme guide natif H3.

Le principe :

```text
latent épisode N
      │
      ├──> Promax · Latent Continuation
      │       ├── positive guidé
      │       └── latent window frais
      │
      └──────────────────────────────┐
                                     │
positive guidé + latent window -> SamplerCustomAdvanced
                                     │
                                     v
                         sampled continuation window
                                     │
                                     v
                         Promax · Append Continuation
                              │                  │
                              │                  └── sampled_window pour décodage
                              v
                       cumulative_latent
                              │
                              v
                       Save AV Latent
```

### Pourquoi il existe un overlap caché

Le nouveau window commence par un petit morceau du latent précédent. Ce morceau donne au modèle le mouvement, la pose, la caméra, l'ambiance et l'audio immédiatement précédents. Il est ensuite supprimé du latent cumulatif ; seule la partie réellement nouvelle est ajoutée.

Réglage de départ recommandé :

- **overlap_frames = 22** → environ 0,917 s de contexte caché.
- **extension_frames = 119** → environ 4,958 s réellement nouvelles.
- window réellement samplé = 141 frames.

La contrainte est :

```text
overlap_frames : 17k+5  -> 5, 22, 39, ...
extension_frames : multiple de 17
window total <= 362 frames
```

Pour un GPU 12 Go, commencer avec `22 + 119`. Augmenter la durée seulement après un rendu stable.

### Règle de prompt pour la continuation

Pour le clip suivant, utiliser le Director en **T2V** pour décrire uniquement la nouvelle mise en scène. Ne pas remettre une première image et ne pas répéter des phrases du type :

> Continue directly from the previous latent with the same exact...

Le latent précédent est déjà le contexte. Le premier plan du nouveau prompt doit plutôt annoncer **la nouvelle vue caméra et la nouvelle action**.

Exemple :

```text
[Shot 1] New camera angle from low starboard side. The fisherman suddenly turns toward the stern as the boat tilts.
[Shot 2] At 00:02.000, handheld close shot toward the water. A dark shape passes beneath the hull and the water rises against the side.
```

Cela réduit le risque que le modèle réinterprète ou remorphe inutilement les personnages au début de chaque génération.

### Wiring continuation

1. Générer le premier clip normalement avec Promax Director + sampler.
2. Connecter le **latent échantillonné final du sampler**, pas le latent vide du Director, à `Promax · Save AV Latent` si l'on veut le conserver.
3. Pour le clip suivant, connecter ce latent directement, ou le recharger avec `Promax · Load AV Latent`.
4. Créer le nouveau prompt avec Promax Director en T2V.
5. `Director.positive` + `previous_latent` → `Promax · Latent Continuation`.
6. `Continuation.positive` → `BasicGuider.conditioning`.
7. `Continuation.latent` → `SamplerCustomAdvanced.latent_image`.
8. Le `SamplerCustomAdvanced.output` → `Promax · Append Continuation.sampled_window`.
9. Le latent précédent → `Promax · Append Continuation.previous_latent`.
10. Relier également les deux compteurs d'overlap du nœud Continuation au nœud Append.
11. Sauvegarder `Append.cumulative_latent` pour l'épisode suivant.
12. Pour exporter uniquement la nouvelle partie, décoder `Append.sampled_window`, puis passer images + audio dans `Promax · Trim Continuation Media` avec `Append.trim_overlap_frames`.

## Sauvegarde du latent AV

`Promax · Save AV Latent` écrit dans `ComfyUI/output/` avec le préfixe par défaut :

```text
Promax/latents/h3_00001_.promaxlatent
```

Le nœud renvoie le chemin relatif exact. Pour le recharger : copier ce chemin dans `Promax · Load AV Latent.file_path`. Le loader cherche dans `ComfyUI/output/` puis dans `ComfyUI/input/`.

Le fichier contient séparément :

- latent vidéo H3,
- latent audio H3,
- métadonnées de version, fps et nombre de frames.

## Exigence ComfyUI pour la continuation

La continuation native exige un ComfyUI assez récent pour les guides MiniMax H3 à position arbitraire, soit **commit `e01fb4c` ou plus récent**. Promax teste cette capacité au lancement de la continuation et renvoie une erreur explicite si la version est trop ancienne.

Aucun patch global de PyTorch, aucune DLL SageAttention et aucun monkey-patch H3 n'est ajouté.

## 12 Go VRAM : choix de conception

Promax privilégie :

- un seul batch,
- références limitées,
- `ref_image_size=match`,
- décodage vidéo tuilé,
- contexte de continuation court,
- latent historique conservé sur son device actuel ; un latent rechargé reste CPU et n'est pas recopié intégralement en VRAM pour l'append,
- pas de SageAttention forcé,
- pas de vidage agressif des modèles globaux.

Le nom `12GB` est une cible d'architecture, pas une certification. Les poids choisis, la version ComfyUI/PyTorch, les autres programmes GPU et la durée influencent le pic réel.

## Références vidéo/audio Ref2V

`reference_video` attend des frames IMAGE à 24 fps. Promax borne la référence pour éviter les gros chargements incontrôlés et réduit sa résolution avant l'encodage H3.

`reference_audio` nécessite l'Audio VAE. Il sert de référence sonore ; ce n'est pas une copie bit à bit de la bande-son d'entrée.

## Validation actuelle

Structure Promax :

- `promax_plan.py` : validation et compilation du scénario.
- `minimax_promax.py` : Director Promax basé sur les classes H3 natives ComfyUI.
- `promax_continuation.py` : continuation native AV + append + save/load `.promaxlatent`.
- `promax_trim.py` : suppression du contexte caché après décodage.
- `js/minimax_promax.js` : interface Promax indépendante.
- `tests/promax` : tests hors GPU de la base Director.

La logique de continuation/save-load a été vérifiée structurellement avec un latent H3 simulé : un clip 124 frames + une extension 119 frames produit un latent cumulatif de 243 frames avec audio aligné. Cela ne remplace pas le test d'inférence réel.

## Premier test recommandé sur RTX 5070 12 Go

### Test A — clip initial

- 480×864
- ~5 s
- seed fixe 42
- un prompt simple
- sampler habituel du workflow
- sauvegarder le **latent échantillonné** avec `Promax · Save AV Latent`

### Test B — continuation

- recharger le latent précédent
- nouveau prompt T2V avec une **nouvelle vue caméra dès Shot 1**
- overlap 22
- extension 119
- même résolution
- même modèle/sampler
- append + save
- décoder le sampled window puis Trim Continuation Media

À relever : temps total, pic VRAM, pic RAM, stabilité visage/personnage, cohérence caméra, raccord audio et présence éventuelle d'un morphing dans la première seconde visible.

Le résultat du test B dira si l'on garde 22 frames de contexte ou si l'on doit tester 5 / 39 frames.
