"""Process protocol: the PARAMOUNT_* environment contract between the launcher, the GUIs, and child scripts.

Child scripts are separate processes; everything they learn from their parent arrives in these
variables. Every name used anywhere in the suite must be declared here (tests/test_process_protocol.py
enforces it). Never rename one: add a new name and keep the old one working.
"""
from __future__ import annotations

PROTOCOL_VERSION = 1

PROTOCOL_VERSION_ENV = "PARAMOUNT_PROTOCOL_VERSION"
SUITE_CONTEXT_ENV = "PARAMOUNT_SUITE_CONTEXT"
RUN_ID_ENV = "PARAMOUNT_RUN_ID"

ENV_VARS: dict[str, str] = {
    # Protocol and suite context (launcher -> GUIs -> children)
    PROTOCOL_VERSION_ENV: "Version of this contract (PROTOCOL_VERSION).",
    SUITE_CONTEXT_ENV: "Path of runtime/suite_context.json (product, firmware, DUT adapter, revisions).",
    RUN_ID_ENV: "Unique id of one child run; also recorded in its run/analysis manifest.",
    "PARAMOUNT_TESTING_SUITE_LAUNCHER": "1 when started by Transparency_Test_Suite_Launcher.py.",
    "PARAMOUNT_LAUNCHED_SUITE": "COLLECTION or ANALYSIS.",
    "PARAMOUNT_PRODUCT_ID": "Selected product id from config/AE_Product_Profiles.json.",
    "PARAMOUNT_PRODUCT_NAME": "Selected product display name.",
    "PARAMOUNT_PRODUCT_PROFILE_FILE": "Path of the product profile file.",
    "PARAMOUNT_PRODUCT_PROFILE_SCHEMA_VERSION": "schema_version of the product profile file.",
    "PARAMOUNT_PRODUCT_RECOMMENDATIONS_JSON": "Product recommendations for this suite, as JSON.",
    "PARAMOUNT_FIRMWARE_VERSION": "Selected DUT firmware version.",
    "PARAMOUNT_DUT_ADAPTER": "DUT command adapter module for that firmware (e.g. AECommandsUniversal).",
    "PARAMOUNT_ADAPTER_COMPATIBILITY": "verified or unverified.",
    "PARAMOUNT_SUITE_ROOT": "Optional repo root hint for scripts that search for libraries.",
    # Collection GUI -> collection scripts
    "PARAMOUNT_UNIFIED_LAUNCHER": "1 when started by the collection GUI.",
    "PARAMOUNT_SELECTED_TEST": "Collection test key (plugin.json tests[].key).",
    "PARAMOUNT_COLLECTION_PARENT_FOLDER": "Folder in which each collection run creates its own run folder.",
    "PARAMOUNT_BENCH_PROFILE": "Active bench profile name.",
    "PARAMOUNT_RISE_FALL_MODE": "Rise/Fall mode: CW, SLP, or DLP.",
    "PARAMOUNT_RISE_FALL_TEST_TYPE": "Legacy alias of PARAMOUNT_RISE_FALL_MODE.",
    "PARAMOUNT_RISE_FALL_EDGE": "Rise/Fall edge: RISE or FALL.",
    "PARAMOUNT_RISE_FALL_TRANSITION": "Legacy alias of PARAMOUNT_RISE_FALL_EDGE.",
    # Bench equipment (suite_common.bench.EQUIPMENT_FIELDS)
    "PARAMOUNT_GENERATOR_ADDRESS": "DUT generator hostname / IP.",
    "PARAMOUNT_GENERATOR_COM_PORT": "DUT generator AEBus COM port.",
    "PARAMOUNT_PSYNC_SLAVE_ADDRESS": "Pulse Sync slave generator hostname / IP.",
    "PARAMOUNT_LYNX_ADDRESS": "Lynx hostname / IP.",
    "PARAMOUNT_NAVIGATOR_PORT": "Navigator / tuner COM port.",
    "PARAMOUNT_OSCILLOSCOPE_ADDRESS": "Oscilloscope IP or VISA resource.",
    "PARAMOUNT_WAVEFORM_GENERATOR_ADDRESS": "Waveform generator IP or VISA resource.",
    "PARAMOUNT_SPECTRUM_ANALYZER_ADDRESS": "Spectrum analyzer IP.",
    "PARAMOUNT_DC_SUPPLY_ADDRESS": "DC supply (BK 9141) IP or VISA resource.",
    # Script-specific overrides read by individual collection scripts
    "PARAMOUNT_AE_COMMANDS_FILE": "RF Ramping: explicit path of the AE command library.",
    "PARAMOUNT_COUNTER_ADDRESS": "RF Tuning: frequency counter address (alias).",
    "PARAMOUNT_FREQUENCY_COUNTER_ADDRESS": "RF Tuning: frequency counter address.",
    "PARAMOUNT_FREQUENCY_COUNTER_RESOURCE": "RF Tuning: frequency counter VISA resource.",
    "PARAMOUNT_FUNCTION_GENERATOR_ADDRESS": "Arc Management: function generator address.",
    "PARAMOUNT_GENERATOR_IP": "RF Tuning: generator address (alias).",
    "PARAMOUNT_NAVIGATOR_ADDRESS": "RF Tuning: Navigator address (alias).",
    "PARAMOUNT_PSYNC_TRIGGER_CHANNEL": "Pulse Sync: scope trigger channel.",
    "PARAMOUNT_TUNER_MAP_PARENT": "Power Profile: tuner map folder.",
    "PARAMOUNT_WARMUP_TIME_S": "Power Linearity: warm-up time in seconds.",
    # Analysis GUI -> analysis scripts
    "PARAMOUNT_UNIFIED_ANALYSIS_LAUNCHER": "1 when started by the analysis GUI.",
    "PARAMOUNT_SELECTED_ANALYSIS": "Analysis key (plugin.json tests[].key).",
    "PARAMOUNT_ANALYSIS_MODE": "INDIVIDUAL, SINGLE, or COMPARE.",
    "PARAMOUNT_DATASET_A_FOLDER": "Dataset A folder (a collection run folder).",
    "PARAMOUNT_DATASET_B_FOLDER": "Dataset B folder for COMPARE.",
    "PARAMOUNT_DATASET_A_LABEL": "Dataset A label.",
    "PARAMOUNT_DATASET_B_LABEL": "Dataset B label.",
    "PARAMOUNT_ANALYSIS_OUTPUT_FOLDER": "Folder for analysis outputs.",
    "PARAMOUNT_POWERPOINT_EXPORT_ENABLED": "1 when the analysis should write a presentation package.",
    "PARAMOUNT_POWERPOINT_RUN_FOLDER": "Presentation run folder.",
    "PARAMOUNT_POWERPOINT_PACKAGE_FOLDER": "This analysis's package folder (analysis_packages/<key>).",
    "PARAMOUNT_POWERPOINT_ANALYSIS_ID": "Analysis key expected in the package manifest.json.",
    "PARAMOUNT_POWERPOINT_SCHEMA_VERSION": "Presentation package schema version.",
    "PARAMOUNT_POWERPOINT_PRESENTATION_TITLE": "Presentation title.",
    "PARAMOUNT_POWERPOINT_DUT_NAME": "DUT name for the presentation.",
    "PARAMOUNT_POWERPOINT_PROJECT_NAME": "Project name for the presentation.",
    "PARAMOUNT_POWERPOINT_THEME_FILE": "PowerPoint builder: .thmx theme override.",
}

# Names built at runtime from a prefix (e.g. f"PARAMOUNT_DATASET_{label}_FOLDER").
DYNAMIC_PREFIXES = ("PARAMOUNT_DATASET_",)


def protocol_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """Copy of `base` (default: none) with the protocol version set."""
    environment = dict(base or {})
    environment[PROTOCOL_VERSION_ENV] = str(PROTOCOL_VERSION)
    return environment


def paramount_settings(environment: dict[str, str]) -> dict[str, str]:
    """The PARAMOUNT_* variables a child was given, for its manifest (large JSON blobs excluded)."""
    return {
        key: value for key, value in sorted(environment.items())
        if key.startswith("PARAMOUNT_") and key != "PARAMOUNT_PRODUCT_RECOMMENDATIONS_JSON"
    }
