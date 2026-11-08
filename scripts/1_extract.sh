#/bin/bash
# EXPORTS CUSTOMIZATIONS
export WORK_ROOT="<YOUR WORK PATH>";
export WOW_RETAIL="<YOUR MODERN CLIENT>";
export HDPORT_TOOLS="$WORK_ROOT/tools/hdport";
export TACT_TOOL="$WORK_ROOT/tools/tacttool";
export EXTRACT_DEST="$WORK_ROOT/wow.export";
export TABLES_SRC="$WORK_ROOT/wow.export";
export GENERATED_DATA="$WORK_ROOT/generated_data";

read -p "Enter chr-model IDs for male and female with a space separator: " male_id female_id
read -p "Enter exact race name in all lowercase: " race_name

# Generate our filelist.

python3 "$HDPORT_TOOLS/inspect_race.py" \
    --tables "$TABLES_SRC" \
    --chr-model $male_id $female_id \
    --extract-list "$GENERATED_DATA/race-list/race_list_$race_name.txt" \
    | tee "$GENERATED_DATA/reports/${race_name}_options.txt"

"$TACT_TOOL/TACTTool" -m list \
      -i "$GENERATED_DATA/race-list/race_list_$race_name.txt" \
      -d "$WOW_RETAIL" \
      -o "$EXTRACT_DEST"

# CAN CHECK WITH
#python3 "$HDPORT_TOOLS/inspect_race.py" --tables "$TABLES_SRC" --chr-model $male_id $female_id --extract-list "$RACE_LIST/race_list_$race_name.txt" --export "$EXTRACT_DEST" --extract-list "$RACE_LIST/race_list_$race_name.txt"
