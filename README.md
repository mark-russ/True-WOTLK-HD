# True-WOTLK-HD
Removes the 16bit indexed drawing limit from the 3.3.5a client and provides a pipeline to backport content from retail, with Worgen &amp; Goblin support.
You will need your private server to be compatible with this too, as it ports several new customization options over, which requires DBC updates.

_For transparency: This guide is very rough and will be updated to be much simpler later. I'm mostly using this as a backup so that nothing is lost. Claude also did the heavylifting for much of this work._

## GUIDE IS NOT FINISHED OR TESTED FULLY!

## Big items you will need
You'll need both the retail client and the 3.3.5a client.

## How to use
To patch your client:
`python3 hd-patcher.py Wow.exe`

## After patching...
Add a line in `WTF/Config.wtf`:

`SET componentTextureLevel "10"`

## Get the tools
You will need the following:
  1. [TACTSharp](https://github.com/wowdev/TACTSharp)
  2. [wow.export](https://github.com/Kruithne/wow.export)

Put these in the `tools` directory. So the directory listing should look like:
```
  hdport
  tacttool
  wowexport
```

## Get the community listfile
Create a new folder `external_data` next to `tools`.
[Download the community listfile.](https://github.com/wowdev/wow-listfile) by checking the "releases" section and getting the one labelled `community-listfile.csv`.
Put the listfile in the `external_data` folder.

## Get the 3.3.5a DBCs
Create a new folder `335a_DBFilesClient` next to `tools`.
Get the path to your DBFilesClient folder in your Wow 3.3.5a directory.
I'm using the [worgoblins module](https://github.com/heyitsbench/mod-worgoblin) so what I need is already extracted. Get yours by either extracting the MPQs yourself (if you don't already have them) or by pulling them out of AzerothCore which also has the DBCs easily accessible.
.../World of Warcraft 3.3.5a/Data/patch-A.MPQ/DBFilesClient
You need: `CharSections.dbc`, `CharHairGeosets.dbc`, `CharacterFacialHairStyles.dbc` and `BarberShopStyle.dbc` in particular. Put these in `335a_DBFilesClient`.

## Get the retail DBCs (as CSV)
Set your `wow.export` settings to export to a folder named `wow.export` in this subfolder (should be next to `tools`).
Open `wow.export`, navigate to the database button at the top. Export all of the DBCs as CSV, starting with all Chr (you can select multiple and extract at once), also export:
```
ComponentTextureFileData.csv
ConditionalChrModel.csv
TextureFileData.csv
```
## Get the retail race assets
Navigate to the models tab and search `character/`. Each race will be in this subfolder. We want all of the playable races that your private server supports.
For each race, export each gender's HD model:
For example, if I search `character/human` I will see a `character/human/female/humanfemale_hd.m2` and `character/human/male/humanmale_hd.m2`. This export should also get you every anim, skel and skin file, etc.
You may encounter a race that doesn't end in `_hd`, if you do, get the regular model, it is `_hd`, don't get `hd_sdr` or `_sdr` models.

## Start the pipeline
For each race that you want to export...

Race	| Retail  |  race ID  |	Male  |	Female
Human | 	1 |	1 |	2
Orc | 	2 |	3 |	4
Dwarf | 	3 |	5 |	6
Night Elf | 	4 |	7 |	8
Undead | 	5 |	9 |	10
Tauren | 	6 |	11 |	12
Gnome | 	7 |	13 |	14
Troll | 	8 |	15 |	16
Goblin | 	9 |	17 |	18
Blood Elf | 	10 |	19 |	20
Draenei | 	11 |	21 |	22
Worgen | 	12 |	23 |	24

## Build the extraction list to automate the texture extractions
Run `./1_extract.sh` for each race. _TODO: Automate this_
You should now have for each race, a list of texture assets for that race in `generated_data/race-list`.

## Run the inspector for each race
THIS STEP IS CONDITIONALLY OPTIONAL: I have included JSON files that were an iterative process between Claude and I, trial and error stuff. This step is not scientific, you can skip it unless you're building a brand new race whose JSON is not included. If you're just building all of the 3.3.5a races, or 3.3.5a + worgen + goblin, you won't need to do the following:
1. Run `./2_report.sh` for each race. This generates a detailed inspection of that race's model. Hand it, along with the `tools/hdport` over to Claude, and ask it to generate a JSON file that matches what the hdport expects.
1. Back up your 3.3.5a MPQ that you're working in.
1. Edit line 10 of `./3_build.sh`, which lists each race that we're building, remove `worgen goblin` from the list if you're not building for worgen and goblins. Add any races that your private server supports, and that you have generated a race list for / extract assets for.
1. Run `./3_build.sh`, this will build all races and update your DBCs on your 3.3.5a client to support their customization options. Also make sure that when it finishes, you copy this over to your server's data directory, replacing its old DBCs.
  _It's safe to rerun `./3_build.sh` several times. It will safely update the customization options each time, it will not duplicate entries in your DBC from multiple runs._
  _At this point, you should be able to restart (or start) your private server and your game client, at character creation, you'll see new options and HD models. NPCs will need to be rebaked though or they'll be jacked up._
1. Edit line 22 of `./3_build.sh`, which lists each race ID we're building, remove `9` and `12` from the list if you're not building for worgen and goblins. Add any races that your private server supports. Refer to the table above for the right race ID, assuming your private server doesn't renumber anything. If you have other races, check the race DBC for its ID.
1. Run `./4_items.sh` to refit the armors to match the HD models.
1. Run `./5_npcs.sh` to update all NPCs to use the HD models.
