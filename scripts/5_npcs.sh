#/bin/bash
export WORK_ROOT="<YOUR WORK PATH>";
export WOW_RETAIL="<YOUR MODERN CLIENT>";
export DBC_SOURCE="<YOUR MPQ FOLDER YOU PLAN ON PATCHING>/DBFilesClient";
export HDPORT_TOOLS="$WORK_ROOT/tools/hdport";
export TACT_TOOL="$WORK_ROOT/tools/tacttool";
export GENERATED_DATA="$WORK_ROOT/generated_data";
export PATCH_DEST="$WORK_ROOT/patch";

read -p "Enter race ID" race_id;
cd "$TACT_TOOL";

./TACTTool -m list \
    -i "$HDPORT_TOOLS/retail_bakes_list.txt" \
    -d "$WOW_RETAIL" \
    -o "$GENERATED_DATA/tact";

python3 "$HDPORT_TOOLS/rebake.py" \
    --dbc "$PATCH_DEST/patch/DBFilesClient" "$DBC_SOURCE" \
    --data "$PATCH_DEST/patch" "$GENERATED_DATA/tact" \
    --out "$GENERATED_DATA/rebake" \
    --race 1 2 3 4 5 6 7 8 9 10 11 \
    --retail-bakes "$HDPORT_TOOLS/retail_bakes.csv" \
    --layout retail1024


#read -p "Enter NPC ID" npc_id;
#python3 "$HDPORT_TOOLS/rebake.py" \
#    --dbc "$PATCH_DEST/patch/DBFilesClient" "$DBC_SOURCE" \
#    --data "$PATCH_DEST/patch" "$GENERATED_DATA/tact" \
#    --out "$GENERATED_DATA/rebake" \
#    --race $race_id \
#    --retail-bakes "$HDPORT_TOOLS/retail_bakes.csv" \
#    --layouts "$PATCH_DEST/layouts" \
#    --only $npc_id --preview npc.png
