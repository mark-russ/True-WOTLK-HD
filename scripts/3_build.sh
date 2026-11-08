#/bin/bash
export WORK_ROOT="<YOUR WORK PATH>";
export CLIENT_PATCH="<YOUR MPQ FOLDER YOU PLAN ON PATCHING>";
export HDPORT_TOOLS="$WORK_ROOT/tools/hdport";
export COMMUNITY_LISTFILE="$WORK_ROOT/external_data/community-listfile.csv"
export EXTRACT_DEST="$WORK_ROOT/wow.export";
export PATCH_DEST="$WORK_ROOT/patch";

python3 "$HDPORT_TOOLS/hdport.py" \
--race forsaken tauren troll goblin bloodelf draenei worgen dwarf gnome human nightelf orc \
--export "$EXTRACT_DEST" \
--tables "$EXTRACT_DEST" \
--listfile "$COMMUNITY_LISTFILE" \
--dbc "$CLIENT_PATCH/DBFilesClient" \
--out "$PATCH_DEST" \
--layout retail1024;

#read -p "Enter race name (all lowercase)" race_name;
#python3 "$HDPORT_TOOLS/hdport.py" \
#--race $race_name \
#--export "$EXTRACT_DEST" \
#--tables "$EXTRACT_DEST" \
#--listfile "$COMMUNITY_LISTFILE" \
#--dbc "$CLIENT_PATCH/DBFilesClient" \
#--out "$PATCH_DEST" \
#--layout retail1024;

echo "Copying assets to client patch...";
cp -a "$PATCH_DEST/patch/." "$CLIENT_PATCH";

echo "MAKE SURE YOU REPLACE YOUR DBCS ON YOUR PRIVATE SERVER WITH THE ONES UPDATED FROM YOUR CLIENT!";
