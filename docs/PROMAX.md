# MiniMax H3 Promax 0.1

Director conçu à neuf dans ce dépôt pour une cible **RTX 5070 12 Go de VRAM**, avec environ **48 Go de RAM système** côté utilisateur. Ce n'est pas une promesse de fonctionnement avec 12 Go de RAM système. Cette version est une **alpha : tests hors GPU effectués, rendu réel et mémoire à mesurer sur la machine cible**.

## Installation

Dans le dossier de ce custom node :

```powershell
git fetch origin
git switch feat/minimax-h3-promax
git pull --ff-only
```

Redémarrer ComfyUI, puis recharger son interface. Le menu `MiniMax H3 / Promax` contient `MiniMax H3 Promax · Director` et `Promax · Last Frame`. Les anciens nœuds restent disponibles.

Pour une nouvelle installation, dans `ComfyUI/custom_nodes` :

```powershell
git clone --branch feat/minimax-h3-promax https://github.com/Kostt3d/ComfyUI-MiniMaxH3-Director.git
```

Ne pas installer une seconde copie si ce dépôt est déjà présent.

## Choisir le workflow

- **`example_workflows/Promax-12GB-4B-Turbo.json`** : variante destinée à la configuration du workflow joint par l'utilisateur. Encodeur 4B, projection ClipProj 4B, deux LoRA Turbo correspondant chacun à leur modèle, 8 étapes Euler/simple. Nécessite le nœud externe `ClipProjApply` déjà utilisé dans ce workflow. La qualité et le gain de vitesse ne sont pas mesurés ici.
- **`example_workflows/Promax-12GB-Base.json`** : modèle H3, encodeur H3 32B, 20 étapes res_multistep/simple, sans LoRA ni ClipProj. Dépend uniquement de ce dépôt et de ComfyUI avec H3 natif. Le 32B exerce une forte pression sur la RAM ; cette base ne garantit pas de tenir dans 48 Go.
- **`Promax-12GB-Base.api.json`** : équivalent API, pas le fichier à glisser dans l'éditeur.

Sélectionner les vrais fichiers installés dans les chargeurs. Les noms préremplis proviennent du workflow fourni et du contexte de la configuration ; ils ne téléchargent rien et ne constituent pas une vérification de compatibilité de chaque quantification.

| Composant | Base | Variante 4B Turbo |
|---|---|---|
| Diffusion T2V / FL2V | `minimax_h3_fl2va_pruned_fp8_scaled.safetensors` | Identique + LoRA FL2V Turbo |
| Diffusion Ref2V | `minimax_h3_ref2va_pruned_fp8_scaled.safetensors` | Identique + LoRA Ref2V Turbo |
| Encodeur | `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | `qwen3vl_4b_fp8_scaled.safetensors` + `mmh3-4b-ClipProj-v3.1.safetensors` |
| VAE vidéo | `minimax_h3_video_vae_fp8mix.safetensors` | Identique |
| VAE audio | `minimax_h3_audio_vae_fp32.safetensors` | Identique |

Un encodeur Qwen générique n'est pas un remplacement direct de l'encodeur H3 : le 4B nécessite sa projection adaptée. Pour la variante Turbo, conserver les noms exacts des deux LoRA du workflow ou choisir leurs équivalents installés et compatibles. Les chargeurs ComfyUI peuvent vérifier l'existence des fichiers des deux branches avant exécution, même si Promax n'évalue que le modèle sélectionné.

## Utilisation

1. Ouvrir le workflow, sélectionner les fichiers disponibles, garder le preset `Balanced portrait 480x864` et les deux plans de démonstration, soit 5 secondes demandées.
2. Dans `Scénario`, remplir le prompt global, l'ambiance sonore et les plans. Chaque plan possède une durée, une caméra, une action et un son/dialogue. Les flèches déplacent les plans ; les coupes et les repères temporels sont recalculés.
3. Choisir le mode :
   - **T2V** : texte vers vidéo/audio, modèle FL2VA. Les sockets de références et keyframes sont inactifs.
   - **FL2V** : texte avec première image obligatoire et dernière image facultative, modèle FL2VA. Ajouter `Load Image`, le relier à `first_frame`. L'image est recadrée au format de sortie, pas étirée.
   - **Ref2V** : modèle REF2VA. Relier au moins une référence, renseigner sa description et ses éléments à conserver dans l'onglet `Références`. Les références image doivent être consécutives depuis `reference_1`. Écrire `<Picture 1>` dans le plan pour la désigner. `summary` est encodé dans ce mode uniquement.
4. Lancer. Le workflow échantillonne le latent AV, décode la vidéo par tuiles et l'audio séparément, puis exporte une vidéo sRGB à 24 fps et une dernière image.
5. Le prompt réellement encodé et les paramètres effectifs apparaissent dans `Contrôle` après exécution. Le storyboard est sauvegardé dans le JSON du workflow, sans fichier caché ni stockage dans le navigateur.

`retained` exprime une consigne au modèle. Ce n'est ni un masque spatial ni une garantie de reproduction exacte. Il n'y a pas de bouton de préservation absolue fictif.

## Formats et durées

- `480×864` est le format déjà employé dans le workflow fourni : il est proche du 9:16, mais son rapport exact est **5:9**.
- `576×1024` donne un **9:16 exact**, avec plus de pixels à traiter.
- `288×512` donne un **9:16 exact** pour un premier test moins lourd.
- Les variantes paysage inversent ces dimensions.
- Toutes les dimensions sont divisibles par 32. Une source horizontale ne peut pas inverser le preset portrait.
- Un clip accepte 4–15 secondes demandées et 1–12 plans. H3 arrondit le nombre de frames à `17k+5` : 5 s demandées donnent 124 frames / 5,167 s ; 15 s donnent 362 frames / 15,083 s. La sortie indique cette différence.
- Les timecodes sont des consignes de storyboard apprises par H3, pas des coupes garanties à la frame près.

## Références vidéo et audio

`reference_video` attend des **frames IMAGE à 24 fps**, entre 5 et 120 frames. Utiliser un chargeur vidéo en amont, le régler à 24 fps et découper le passage à 5 s maximum. Promax réduit la résolution avant l'encodage H3, mais le chargement d'une grosse vidéo en amont peut déjà occuper beaucoup de RAM. H3 peut encore raccourcir la référence à sa grille temporelle native.

`reference_audio` accepte jusqu'à 15 s et exige `audio_vae`. Il sert de référence sonore ; le workflow exporte l'audio **généré**, pas une copie bit à bit de la bande-son d'entrée. Le socket vidéo ne récupère pas automatiquement la bande-son du fichier.

## Ce qui réduit la charge

- Évaluation paresseuse du seul modèle et du seul LoRA correspondant au mode. Aucun repli silencieux sur le mauvais type de poids.
- Références H3 en `match`, jamais `max` ; trois images maximum, une vidéo courte, une référence audio.
- Durée et définition bornées, batch de génération unique.
- Décodage vidéo natif par tuiles spatiales et temporelles (256 / 64 / 32 / 8).
- Aucun patch SageAttention, changement global de PyTorch, vidage forcé des modèles ou changement des paramètres système.

ComfyUI reste responsable de l'offload GPU. Les autres tâches GPU, la version de ComfyUI/PyTorch, les poids, la RAM et le nombre de références influencent la mémoire et la vitesse. Réduire d'abord la durée et la résolution si le test dépasse la mémoire disponible. Ne pas considérer le nom « 12GB » comme une certification.

## Continuité entre clips

La dernière image est sauvegardée dans `output/Promax`. La recharger sur `first_frame` en mode FL2V pour le clip suivant. Choisir une nouvelle caméra dans le premier plan si le scénario le demande ; un changement brutal reste un risque de transition.

**Cette version ne fournit pas de continuation latente, d'éditeur de retakes, d'upscaler latent ni de FaceRefine.** Le latent de sortie est le latent AV initial destiné au sampler, pas celui du rendu précédent. Le nœud ne copie pas le moteur de continuation externe ni l'ancien Director sous un nouveau nom. Ces limites sont explicites afin de ne pas promettre une continuité qui n'est pas implémentée.

## Architecture et validation

- `promax_plan.py` : validation et compilation pure des plans vers les champs H3.
- `minimax_promax.py` : nœuds indépendants utilisant les classes H3 natives de ComfyUI, via le petit module existant `minimax_core.py` de découverte des API.
- `js/minimax_promax.js` : interface propre à Promax, sans héritage de la grande timeline précédente.
- `tests/promax` : tests hors GPU de sélection paresseuse, compilation, contraintes, appels natifs simulés, sérialisation de l'éditeur et câblage des workflows.

```sh
python -m unittest discover -s tests/promax -p 'test_*.py' -v
node tests/promax/test_editor.cjs
```

Ces tests ne validentent pas les poids, le rendu réel, la compatibilité de toutes les versions de ComfyUI, la mise en page réelle dans un navigateur ni un pic VRAM de 12 Go. Première mesure à effectuer : 5 s / 480×864 / seed 42, puis relever temps total, pic VRAM/RAM, dimensions, nombre de frames et qualité audio/vidéo.
