#!/usr/bin/env bash
# Read-only, bounded sampling. Redirect stdout to private evidence, never a public artifact.
set +x
set -Eeuo pipefail
trap 'printf "Capacity collection failed at line %s; retain partial evidence.\n" "$LINENO" >&2' ERR
if [[ $# != 4 || ! "$1" =~ ^[0-9]+$ || "$1" -lt 30 || "$1" -gt 900 ]]; then
  printf 'Usage: collect-capacity.sh SECONDS SONAR_CONTAINER DATABASE_CONTAINER API_HELPER\n' >&2
  exit 2
fi
duration=$1
sonar_container=$2
database_container=$3
helper=$4
[[ "$sonar_container" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]+$ && "$database_container" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]+$ ]]
[[ -f "$helper" && ! -L "$helper" ]]
source "$helper"
declare -F api >/dev/null
for tool in docker jq awk findmnt df date paste sleep; do command -v "$tool" >/dev/null; done

device=$(findmnt -n -r -o MAJ:MIN / | awk '{print $1}')
boot=$(< /proc/sys/kernel/random/boot_id)
started=$(date +%s)
deadline=$((started + duration))

pressure_total() {
  awk '$1 == "some" { sub("total=", "", $5); print $5; found=1 } END { if (!found) exit 1 }' "$1"
}

container_sample() {
  local role=$1 name=$2 state pid relative group usage cpu throttled events io
  state=$(docker inspect "$name" | jq -ce 'if length == 1 then .[0] else error("Container identity") end')
  pid=$(jq -er '.State.Pid | select(. > 0)' <<< "$state")
  relative=$(awk -F: '$1 == "0" { print $3 }' "/proc/$pid/cgroup")
  [[ "$relative" == /* && "$relative" != *..* && "$relative" != / ]]
  group="/sys/fs/cgroup$relative"
  [[ -d "$group" && -r "$group/memory.current" ]]
  usage=$(< "$group/memory.current")
  cpu=$(awk '$1 == "usage_usec" { print $2 }' "$group/cpu.stat")
  throttled=$(awk '$1 == "throttled_usec" { print $2 }' "$group/cpu.stat")
  events=$(awk '{print "\"" $1 "\":" $2}' "$group/memory.events" | paste -sd,)
  io=$(awk '{for(i=2;i<=NF;i++){split($i,a,"=");if(a[1]=="rbytes")r+=a[2];if(a[1]=="wbytes")w+=a[2]}} END{printf "{\"readBytes\":%.0f,\"writeBytes\":%.0f}",r,w}' "$group/io.stat")
  jq -cn --arg role "$role" --argjson state "$state" --argjson usage "$usage" --argjson cpu "$cpu" \
    --argjson throttled "$throttled" --argjson events "{$events}" --argjson io "$io" \
    '{role:$role,id:$state.Id,startedAt:$state.State.StartedAt,status:$state.State.Status,
      health:($state.State.Health.Status // "missing"),restarts:$state.RestartCount,oomKilled:$state.State.OOMKilled,
      memoryLimitBytes:$state.HostConfig.Memory,cpuLimit:($state.HostConfig.NanoCpus/1000000000),
      memoryBytes:$usage,cpuUsageUs:$cpu,throttledUs:$throttled,memoryEvents:$events,io:$io}'
}

while (( $(date +%s) < deadline )); do
  at=$(date -u +%FT%T.%3NZ)
  uptime=$(awk '{print $1}' /proc/uptime)
  cpu=$(awk '/^cpu / {for(i=2;i<=9;i++)total+=$i; printf "{\"total\":%.0f,\"idle\":%.0f,\"ioWait\":%.0f,\"steal\":%.0f}",total,$5,$6,$9}' /proc/stat)
  memory=$(awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/ {gsub(":","",$1);printf "\"%s\":%.0f,",$1,$2*1024}' /proc/meminfo)
  memory="{${memory%,}}"
  disk=$(awk -v device="$device" '($1 ":" $2)==device {printf "{\"readBytes\":%.0f,\"writeBytes\":%.0f,\"busyMs\":%.0f}",$6*512,$10*512,$13;found=1} END{if(!found)exit 1}' /proc/diskstats)
  space=$(df -B1 --output=size,avail / | awk 'NR==2 {printf "{\"total\":%.0f,\"available\":%.0f}",$1,$2}')
  containers=$(printf '%s\n' "$(container_sample sonar "$sonar_container")" "$(container_sample database "$database_container")" | jq -s .)
  health=$(api system/health /dev/stdout | jq -er '.health')
  tasks=$(api 'ce/activity?ps=100&status=PENDING,IN_PROGRESS' /dev/stdout | jq -ce '{total:.paging.total,tasks:[.tasks[] | {id,componentKey,status,submittedAt,startedAt}]}')
  database_ready=false
  if docker exec "$database_container" pg_isready -q; then database_ready=true; fi
  jq -cn --arg at "$at" --arg boot "$boot" --arg health "$health" --argjson uptime "$uptime" \
    --argjson cpu "$cpu" --argjson memory "$memory" --argjson disk "$disk" --argjson space "$space" \
    --argjson cpuPressure "$(pressure_total /proc/pressure/cpu)" \
    --argjson memoryPressure "$(pressure_total /proc/pressure/memory)" \
    --argjson ioPressure "$(pressure_total /proc/pressure/io)" \
    --argjson containers "$containers" --argjson tasks "$tasks" --argjson databaseReady "$database_ready" \
    '{schemaVersion:1,at:$at,boot:$boot,uptime:$uptime,cpu:$cpu,memory:$memory,disk:$disk,space:$space,
      pressureUs:{cpu:$cpuPressure,memory:$memoryPressure,io:$ioPressure},containers:$containers,
      health:$health,databaseReady:$databaseReady,queue:$tasks}'
  sleep 1
done
