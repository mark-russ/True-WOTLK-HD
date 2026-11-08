#/bin/bash
export WORK_ROOT="<YOUR WORK PATH>";
export HDPORT_TOOLS="$WORK_ROOT/tools/hdport";
export COMMUNITY_LISTFILE="$WORK_ROOT/external_data/community-listfile.csv"
export EXTRACT_DEST="$WORK_ROOT/wow.export";
export TABLES_SRC="$WORK_ROOT/wow.export";
export GENERATED_DATA="$WORK_ROOT/generated_data";

read -p "Enter exact race name in all lowercase: " race_name
male_model=$(find "$EXTRACT_DEST/character/$race_name/male" -type f -name "*.m2" | head -n 1);
female_model=$(find "$EXTRACT_DEST/character/$race_name/female" -type f -name "*.m2" | head -n 1);

python3 "$HDPORT_TOOLS/inspect_race.py" \
    --models \
    "$male_model" \
    "$female_model" \
     | tee "$GENERATED_DATA/inspections/${race_name}.txt"


#python3 "$HDPORT_TOOLS/inspect_race.py" \
#    --tables "$TABLES_SRC" \
#    --race "$race_name" \
#    --listfile "$COMMUNITY_LISTFILE" \
#    --export "$EXTRACT_DEST" \
#     | tee "$GENERATED_DATA/${race_name}_inspect.txt"
