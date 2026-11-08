#/bin/bash
export WORK_ROOT="<YOUR WORK PATH>";
export WOW_RETAIL="<YOUR MODERN CLIENT>";
export DBC_SOURCE="<YOUR MPQ FOLDER YOU PLAN ON PATCHING>/DBFilesClient";
export COMMUNITY_LISTFILE="$WORK_ROOT/external_data/community-listfile.csv"
export HDPORT_TOOLS="$WORK_ROOT/tools/hdport";
export TACT_TOOL="$WORK_ROOT/tools/tacttool";
export GENERATED_DATA="$WORK_ROOT/generated_data";
export PATCH_DEST="$WORK_ROOT/patch";

python3 "$HDPORT_TOOLS/itemlist.py" \
    --complete "$GENERATED_DATA/items_output" \
    --listfile "$COMMUNITY_LISTFILE" \
    --out "$GENERATED_DATA/items_output"

python3 "$HDPORT_TOOLS/items.py" \
    --export "$GENERATED_DATA/items_output" \
    --patch "$PATCH_DEST"
    --dbc "$PATCH_DEST/DBFilesClient"
    --listfile "$COMMUNITY_LISTFILE"
    --folders "$GENERATED_DATA/item/objectcomponents/head"
    --out "$GENERATED_DATA/item_build_items"
