import { createHash } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { once } from "node:events";
import path from "node:path";
import { fileURLToPath } from "node:url";
import readline from "node:readline";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPOSITORY_ROOT = path.resolve(SCRIPT_DIR, "..");
const PLAN_DIRECTORY = path.join(REPOSITORY_ROOT, "protocols", "capture-plan");
const JOBS_DIRECTORY = path.join(PLAN_DIRECTORY, "jobs");

const MANIFEST_VERSION = "1.0.0";
const ALLOCATION_SEED =
  "asvspoof5-playback-extension-condition-allocation-v1";
const SOURCE_REVISION = "5d4b1565bc0e3e79343af0b5eacc0ea395405d59";
const CONDITIONS = ["HH", "HL", "LH", "LL"];

const PARTITIONS = [
  {
    name: "train",
    audioDirectory: "flac_T",
    protocol:
      "original-asvspoof5/protocols/ASVspoof5.train.tsv",
    expectedRows: 182_357,
  },
  {
    name: "development",
    audioDirectory: "flac_D",
    protocol:
      "original-asvspoof5/protocols/ASVspoof5.dev.track_1.tsv",
    expectedRows: 140_950,
  },
  {
    name: "evaluation",
    audioDirectory: "flac_E_eval",
    protocol:
      "original-asvspoof5/protocols/ASVspoof5.eval.track_1.tsv",
    expectedRows: 680_774,
  },
];

const COLUMNS = [
  "job_id",
  "capture_order",
  "source_partition",
  "source_protocol_row",
  "source_audio_path",
  "source_file_name",
  "source_family_id",
  "playback_condition",
  "output_audio_path",
  "output_file_name",
  "output_format",
  "content_origin_label",
  "channel_label",
  "content_channel_category",
  "liveness_label",
  "allocation_stratum_id",
  "asvspoof5_speaker_id",
  "asvspoof5_flac_file_name",
  "asvspoof5_speaker_gender",
  "asvspoof5_codec",
  "asvspoof5_codec_q",
  "asvspoof5_codec_seed",
  "asvspoof5_attack_tag",
  "asvspoof5_attack_label",
  "asvspoof5_key",
  "asvspoof5_tmp",
];

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function toPosixPath(...segments) {
  return path.posix.join(...segments);
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function assertTsvValue(value, column) {
  const text = String(value);
  assert(
    !/[\t\r\n]/u.test(text),
    `TSV-unsafe value in ${column}: ${JSON.stringify(text)}`,
  );
  return text;
}

function increment(object, key, amount = 1) {
  object[key] = (object[key] ?? 0) + amount;
}

async function writeChunk(stream, chunk) {
  if (!stream.write(chunk)) {
    await once(stream, "drain");
  }
}

async function closeStream(stream) {
  stream.end();
  await once(stream, "finish");
}

async function hashFile(filePath) {
  const hash = createHash("sha256");
  const stream = createReadStream(filePath);
  for await (const chunk of stream) {
    hash.update(chunk);
  }
  return hash.digest("hex");
}

async function readPartition(partition) {
  const protocolPath = path.join(REPOSITORY_ROOT, partition.protocol);
  const audioDirectoryPath = path.join(
    REPOSITORY_ROOT,
    partition.audioDirectory,
  );
  const audioNames = await readdir(audioDirectoryPath);
  const audioNameSet = new Set(audioNames);

  assert(
    audioNameSet.size === audioNames.length,
    `${partition.audioDirectory} contains duplicate directory entries`,
  );
  assert(
    audioNames.length === partition.expectedRows,
    `${partition.name}: expected ${partition.expectedRows} FLAC files, found ${audioNames.length}`,
  );
  assert(
    audioNames.every((name) => name.endsWith(".flac")),
    `${partition.audioDirectory} contains a non-FLAC entry`,
  );

  const rows = [];
  const seenSourceIds = new Set();
  const input = createReadStream(protocolPath, { encoding: "utf8" });
  const lines = readline.createInterface({ input, crlfDelay: Infinity });

  let rowNumber = 0;
  for await (const line of lines) {
    if (!line.trim()) {
      continue;
    }

    rowNumber += 1;
    const fields = line.trim().split(/\s+/u);
    assert(
      fields.length === 10,
      `${partition.protocol}:${rowNumber}: expected 10 fields, found ${fields.length}`,
    );

    const [
      speakerId,
      fileId,
      speakerGender,
      codec,
      codecQ,
      codecSeed,
      attackTag,
      attackLabel,
      key,
      tmp,
    ] = fields;
    const sourceFileName = `${fileId}.flac`;

    assert(
      !seenSourceIds.has(fileId),
      `${partition.protocol}:${rowNumber}: duplicate source ID ${fileId}`,
    );
    assert(
      audioNameSet.has(sourceFileName),
      `${partition.protocol}:${rowNumber}: missing ${partition.audioDirectory}/${sourceFileName}`,
    );
    assert(
      key === "bonafide" || key === "spoof",
      `${partition.protocol}:${rowNumber}: unsupported KEY ${key}`,
    );

    seenSourceIds.add(fileId);
    rows.push({
      rowNumber,
      speakerId,
      fileId,
      speakerGender,
      codec,
      codecQ,
      codecSeed,
      attackTag,
      attackLabel,
      key,
      tmp,
      sourceFileName,
      sourceFamilyId: codecSeed === "-" ? fileId : codecSeed,
    });
  }

  assert(
    rows.length === partition.expectedRows,
    `${partition.name}: expected ${partition.expectedRows} protocol rows, found ${rows.length}`,
  );
  assert(
    seenSourceIds.size === audioNameSet.size,
    `${partition.name}: protocol/audio cardinality mismatch`,
  );

  return rows;
}

function describeFamilyShape(family, partitionName) {
  const codecPairs = family.rows
    .map((row) => `${row.codec}:${row.codecQ}`)
    .sort();
  const codecNames = new Set(family.rows.map((row) => row.codec));

  if (family.rows.length === 1) {
    assert(
      family.rows[0].codec === "-",
      `${partitionName}/${family.id}: singleton codec family is not uncoded`,
    );
    return "uncoded-only";
  }

  if (family.rows.length === 2) {
    const codedRows = family.rows.filter((row) => row.codec !== "-");
    assert(
      codedRows.length === 1 && codecNames.has("-"),
      `${partitionName}/${family.id}: invalid two-member codec family`,
    );
    return `uncoded-pair:${codedRows[0].codec}:${codedRows[0].codecQ}`;
  }

  if (family.rows.length === 12) {
    const expectedCodecs = [
      "-",
      "C01",
      "C02",
      "C03",
      "C04",
      "C05",
      "C06",
      "C07",
      "C08",
      "C09",
      "C10",
      "C11",
    ];
    assert(
      expectedCodecs.every((codec) => codecNames.has(codec)) &&
        codecNames.size === expectedCodecs.length,
      `${partitionName}/${family.id}: invalid full codec family ${codecPairs.join(",")}`,
    );
    return "full-codec-set";
  }

  throw new Error(
    `${partitionName}/${family.id}: unsupported codec family size ${family.rows.length}`,
  );
}

function allocateConditions(rows, partitionName) {
  const families = new Map();
  for (const row of rows) {
    let family = families.get(row.sourceFamilyId);
    if (!family) {
      family = { id: row.sourceFamilyId, rows: [] };
      families.set(row.sourceFamilyId, family);
    }
    family.rows.push(row);
  }

  const strata = new Map();
  const stratumIdToText = new Map();

  for (const family of families.values()) {
    const first = family.rows[0];
    for (const row of family.rows) {
      assert(
        row.speakerId === first.speakerId &&
          row.speakerGender === first.speakerGender &&
          row.key === first.key &&
          row.attackTag === first.attackTag &&
          row.attackLabel === first.attackLabel,
        `${partitionName}/${family.id}: source family crosses a protected metadata field`,
      );
    }

    const hasUncodedMember = family.rows.some(
      (row) => row.fileId === family.id && row.codec === "-",
    );
    assert(
      hasUncodedMember,
      `${partitionName}/${family.id}: codec family lacks its uncoded seed row`,
    );

    const shape = describeFamilyShape(family, partitionName);
    const stratumText = [
      partitionName,
      first.speakerId,
      first.speakerGender,
      first.key,
      first.attackLabel,
      first.attackTag,
      shape,
    ].join("|");
    const stratumId = sha256(stratumText).slice(0, 16);
    const previousText = stratumIdToText.get(stratumId);
    assert(
      previousText === undefined || previousText === stratumText,
      `allocation stratum hash collision: ${stratumId}`,
    );
    stratumIdToText.set(stratumId, stratumText);
    family.stratumId = stratumId;

    let stratum = strata.get(stratumText);
    if (!stratum) {
      stratum = [];
      strata.set(stratumText, stratum);
    }
    stratum.push(family);
  }

  const allocationAudit = {
    sourceFamilies: families.size,
    strata: strata.size,
    maximumFamilyCountDifferenceWithinStratum: 0,
    maximumJobCountDifferenceWithinStratum: 0,
  };

  const emptyConditionCounts = () =>
    Object.fromEntries(CONDITIONS.map((condition) => [condition, 0]));
  const runningCounts = {
    partition: emptyConditionCounts(),
    byKey: new Map(),
    byAttack: new Map(),
    bySpeaker: new Map(),
  };
  const getScopeCounts = (map, key) => {
    let counts = map.get(key);
    if (!counts) {
      counts = emptyConditionCounts();
      map.set(key, counts);
    }
    return counts;
  };

  for (const stratumText of [...strata.keys()].sort()) {
    const stratum = strata.get(stratumText);
    stratum.sort((left, right) => {
      const leftHash = sha256(`${ALLOCATION_SEED}|${left.id}`);
      const rightHash = sha256(`${ALLOCATION_SEED}|${right.id}`);
      return leftHash.localeCompare(rightHash) || left.id.localeCompare(right.id);
    });

    const tieRotation =
      Number.parseInt(
        sha256(`${ALLOCATION_SEED}|${stratumText}`).slice(0, 8),
        16,
      ) % CONDITIONS.length;
    const deterministicTieOrder = CONDITIONS.map(
      (_, index) => CONDITIONS[(index + tieRotation) % CONDITIONS.length],
    );
    const tieRank = Object.fromEntries(
      deterministicTieOrder.map((condition, index) => [condition, index]),
    );
    const first = stratum[0].rows[0];
    const keyCounts = getScopeCounts(runningCounts.byKey, first.key);
    const attackCounts = getScopeCounts(
      runningCounts.byAttack,
      `${first.key}|${first.attackLabel}`,
    );
    const speakerCounts = getScopeCounts(
      runningCounts.bySpeaker,
      first.speakerId,
    );
    const orderedConditions = [...CONDITIONS].sort(
      (left, right) =>
        attackCounts[left] - attackCounts[right] ||
        keyCounts[left] - keyCounts[right] ||
        speakerCounts[left] - speakerCounts[right] ||
        runningCounts.partition[left] - runningCounts.partition[right] ||
        tieRank[left] - tieRank[right],
    );
    const familyCounts = Object.fromEntries(
      CONDITIONS.map((condition) => [condition, 0]),
    );
    const jobCounts = Object.fromEntries(
      CONDITIONS.map((condition) => [condition, 0]),
    );

    stratum.forEach((family, index) => {
      const condition = orderedConditions[index % orderedConditions.length];
      increment(familyCounts, condition);
      increment(jobCounts, condition, family.rows.length);
      increment(runningCounts.partition, condition, family.rows.length);
      increment(keyCounts, condition, family.rows.length);
      increment(attackCounts, condition, family.rows.length);
      increment(speakerCounts, condition, family.rows.length);
      for (const row of family.rows) {
        row.playbackCondition = condition;
        row.allocationStratumId = family.stratumId;
      }
    });

    const familyValues = Object.values(familyCounts);
    const jobValues = Object.values(jobCounts);
    const familyDifference = Math.max(...familyValues) - Math.min(...familyValues);
    const jobDifference = Math.max(...jobValues) - Math.min(...jobValues);
    allocationAudit.maximumFamilyCountDifferenceWithinStratum = Math.max(
      allocationAudit.maximumFamilyCountDifferenceWithinStratum,
      familyDifference,
    );
    allocationAudit.maximumJobCountDifferenceWithinStratum = Math.max(
      allocationAudit.maximumJobCountDifferenceWithinStratum,
      jobDifference,
    );
    assert(
      familyDifference <= 1,
      `${partitionName}: unbalanced allocation stratum ${stratumText}`,
    );
  }

  for (const family of families.values()) {
    assert(
      new Set(family.rows.map((row) => row.playbackCondition)).size === 1,
      `${partitionName}/${family.id}: codec family split across conditions`,
    );
  }

  return allocationAudit;
}

function createPlanRow(row, partition, captureOrder) {
  const isHuman = row.key === "bonafide";
  const contentOrigin = isHuman ? "human" : "spoof";
  const category = isHuman ? "PH" : "PS";
  const outputFileName = `${row.fileId}_${category}_playback_${row.playbackCondition}.flac`;
  const sourceAudioPath = toPosixPath(
    partition.audioDirectory,
    row.sourceFileName,
  );
  const outputAudioPath = toPosixPath(
    "playback_flac",
    partition.name,
    row.playbackCondition,
    category,
    outputFileName,
  );

  return {
    job_id: `ASV5P-${row.fileId}-${category}-${row.playbackCondition}`,
    capture_order: captureOrder,
    source_partition: partition.name,
    source_protocol_row: row.rowNumber,
    source_audio_path: sourceAudioPath,
    source_file_name: row.sourceFileName,
    source_family_id: row.sourceFamilyId,
    playback_condition: row.playbackCondition,
    output_audio_path: outputAudioPath,
    output_file_name: outputFileName,
    output_format: "flac",
    content_origin_label: contentOrigin,
    channel_label: "playback",
    content_channel_category: category,
    liveness_label: "playback",
    allocation_stratum_id: row.allocationStratumId,
    asvspoof5_speaker_id: row.speakerId,
    asvspoof5_flac_file_name: row.fileId,
    asvspoof5_speaker_gender: row.speakerGender,
    asvspoof5_codec: row.codec,
    asvspoof5_codec_q: row.codecQ,
    asvspoof5_codec_seed: row.codecSeed,
    asvspoof5_attack_tag: row.attackTag,
    asvspoof5_attack_label: row.attackLabel,
    asvspoof5_key: row.key,
    asvspoof5_tmp: row.tmp,
  };
}

async function writePartitionShards(partition, rows) {
  const streams = {};
  const shardState = {};

  for (const condition of CONDITIONS) {
    const fileName = `${partition.name}.${condition}.tsv`;
    const relativePath = toPosixPath("jobs", fileName);
    const absolutePath = path.join(JOBS_DIRECTORY, fileName);
    const stream = createWriteStream(absolutePath, {
      encoding: "utf8",
      flags: "w",
    });
    streams[condition] = stream;
    shardState[condition] = {
      partition: partition.name,
      playback_condition: condition,
      path: relativePath,
      row_count: 0,
      categories: { PH: 0, PS: 0 },
      content_origins: { human: 0, spoof: 0 },
      sha256: null,
    };
    await writeChunk(stream, `${COLUMNS.join("\t")}\n`);
  }

  const seenJobIds = new Set();
  const seenOutputPaths = new Set();
  for (const row of rows) {
    const state = shardState[row.playbackCondition];
    const captureOrder = state.row_count + 1;
    const planRow = createPlanRow(row, partition, captureOrder);

    assert(!seenJobIds.has(planRow.job_id), `duplicate job ID ${planRow.job_id}`);
    assert(
      !seenOutputPaths.has(planRow.output_audio_path),
      `duplicate output path ${planRow.output_audio_path}`,
    );
    seenJobIds.add(planRow.job_id);
    seenOutputPaths.add(planRow.output_audio_path);

    const values = COLUMNS.map((column) =>
      assertTsvValue(planRow[column], column),
    );
    await writeChunk(streams[row.playbackCondition], `${values.join("\t")}\n`);
    state.row_count += 1;
    increment(state.categories, planRow.content_channel_category);
    increment(state.content_origins, planRow.content_origin_label);
  }

  await Promise.all(Object.values(streams).map(closeStream));

  for (const condition of CONDITIONS) {
    const state = shardState[condition];
    state.sha256 = await hashFile(path.join(PLAN_DIRECTORY, state.path));
    assert(
      state.categories.PH > 0 && state.categories.PS > 0,
      `${partition.name}/${condition}: both PH and PS must be represented`,
    );
  }

  return CONDITIONS.map((condition) => shardState[condition]);
}

function buildTotals(shards) {
  const totals = {
    jobs: 0,
    categories: { PH: 0, PS: 0 },
    content_origins: { human: 0, spoof: 0 },
    playback_conditions: Object.fromEntries(
      CONDITIONS.map((condition) => [condition, 0]),
    ),
    partitions: {},
  };

  for (const shard of shards) {
    totals.jobs += shard.row_count;
    increment(totals.categories, "PH", shard.categories.PH);
    increment(totals.categories, "PS", shard.categories.PS);
    increment(totals.content_origins, "human", shard.content_origins.human);
    increment(totals.content_origins, "spoof", shard.content_origins.spoof);
    increment(
      totals.playback_conditions,
      shard.playback_condition,
      shard.row_count,
    );

    const partitionTotals = (totals.partitions[shard.partition] ??= {
      jobs: 0,
      categories: { PH: 0, PS: 0 },
      playback_conditions: Object.fromEntries(
        CONDITIONS.map((condition) => [condition, 0]),
      ),
    });
    partitionTotals.jobs += shard.row_count;
    increment(partitionTotals.categories, "PH", shard.categories.PH);
    increment(partitionTotals.categories, "PS", shard.categories.PS);
    increment(
      partitionTotals.playback_conditions,
      shard.playback_condition,
      shard.row_count,
    );
  }

  return totals;
}

async function main() {
  await mkdir(JOBS_DIRECTORY, { recursive: true });

  const shards = [];
  const allocationAudit = {};
  for (const partition of PARTITIONS) {
    const rows = await readPartition(partition);
    allocationAudit[partition.name] = allocateConditions(rows, partition.name);
    shards.push(...(await writePartitionShards(partition, rows)));
  }

  const totals = buildTotals(shards);
  const expectedTotal = PARTITIONS.reduce(
    (sum, partition) => sum + partition.expectedRows,
    0,
  );
  assert(
    totals.jobs === expectedTotal,
    `expected ${expectedTotal} jobs, generated ${totals.jobs}`,
  );

  const schemaPath = path.join(PLAN_DIRECTORY, "capture-plan.schema.json");
  const schema = JSON.parse(await readFile(schemaPath, "utf8"));
  assert(
    schema.properties &&
      COLUMNS.every((column) => schema.properties[column]) &&
      schema.required?.length === COLUMNS.length,
    "capture-plan.schema.json does not describe every TSV column",
  );

  const index = {
    manifest_version: MANIFEST_VERSION,
    title: "ASVspoof 5 Track 1 playback recapture plan",
    scope: {
      source_database: "ASVspoof 5",
      source_track: 1,
      selection_criterion: "all_retained_track_1_rows",
      selected_source_rows: totals.jobs,
      excluded_source_rows: 0,
      partitions_preserved: true,
    },
    source: {
      upstream_revision: SOURCE_REVISION,
      immutable: true,
      protocols: PARTITIONS.map((partition) => ({
        partition: partition.name,
        path: partition.protocol,
        rows: partition.expectedRows,
        audio_directory: partition.audioDirectory,
      })),
    },
    file_format: {
      index: "json",
      jobs: "tsv",
      encoding: "UTF-8",
      line_ending: "LF",
      delimiter: "TAB",
      header: true,
      schema: "capture-plan.schema.json",
      columns: COLUMNS,
      sha256_scope: "complete shard bytes, including header and LF line endings",
    },
    capture_action: "playback_and_recapture",
    output: {
      root_directory: "playback_flac",
      directory_template:
        "playback_flac/<source_partition>/<playback_condition>/<content_channel_category>/",
      filename_template:
        "<asvspoof5_flac_file_name>_<content_channel_category>_playback_<playback_condition>.flac",
      required_format: "flac",
      source_files_must_not_be_modified: true,
    },
    clean_reference_policy: {
      bonafide_source_role:
        "Each bona fide source remains the immutable CH clean reference and is also the source of exactly one PH capture job.",
      clean_copy_job_created: false,
      spoof_source_role:
        "Each spoof source is a CS source reference and becomes an operational PS sample only after playback and recapture.",
    },
    labels: {
      PH: {
        content_origin_label: "human",
        channel_label: "playback",
        liveness_label: "playback",
      },
      PS: {
        content_origin_label: "spoof",
        channel_label: "playback",
        liveness_label: "playback",
      },
    },
    playback_conditions: {
      HH: {
        playback_device_quality: "high",
        recording_device_quality: "high",
        methodology_playback_reference: "Yamaha HS5",
        methodology_recording_reference: "Audio-Technica AT2020",
      },
      HL: {
        playback_device_quality: "high",
        recording_device_quality: "low",
        methodology_playback_reference: "Yamaha HS5",
        methodology_recording_reference: null,
      },
      LH: {
        playback_device_quality: "low",
        recording_device_quality: "high",
        methodology_playback_reference: null,
        methodology_recording_reference: "Audio-Technica AT2020",
      },
      LL: {
        playback_device_quality: "low",
        recording_device_quality: "low",
        methodology_playback_reference: null,
        methodology_recording_reference: null,
      },
    },
    equipment_binding: {
      status: "deferred_to_acquisition_configuration",
      exact_low_quality_devices: null,
      audio_interface_or_soundcard: null,
      device_serials: null,
      rule:
        "The future recorder must bind each condition label to a documented equipment chain without changing sample assignments.",
    },
    allocation: {
      seed: ALLOCATION_SEED,
      assignment_unit:
        "source family: CODEC_SEED when present, otherwise FLAC_FILE_NAME",
      family_integrity:
        "All codec variants of one source family are assigned to the same playback condition.",
      strata: [
        "source_partition",
        "asvspoof5_speaker_id",
        "asvspoof5_speaker_gender",
        "asvspoof5_key",
        "asvspoof5_attack_label",
        "asvspoof5_attack_tag",
        "codec_family_shape",
      ],
      codec_family_shape:
        "uncoded-only; uncoded-pair:<CODEC>:<CODEC_Q>; or full-codec-set",
      algorithm:
        "Process strata in lexical order; within each stratum, sort source families by SHA-256(seed|family_id), order conditions by the running attack, KEY, speaker, and partition job counts with a SHA-256 tie-break, then assign round-robin.",
      maximum_family_count_difference_per_stratum: 1,
      audit: allocationAudit,
    },
    execution_order: {
      conditions: CONDITIONS,
      partitions: PARTITIONS.map((partition) => partition.name),
      capture_order_scope: "within one partition/condition shard",
    },
    totals,
    shards,
  };

  await writeFile(
    path.join(PLAN_DIRECTORY, "capture-plan.json"),
    `${JSON.stringify(index, null, 2)}\n`,
    "utf8",
  );

  process.stdout.write(
    `${JSON.stringify(
      {
        manifest: toPosixPath(
          "protocols",
          "capture-plan",
          "capture-plan.json",
        ),
        jobs: totals.jobs,
        playback_conditions: totals.playback_conditions,
        categories: totals.categories,
        shards: shards.length,
      },
      null,
      2,
    )}\n`,
  );
}

await main();
