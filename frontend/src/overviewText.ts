/** Every figure and claim on the Overview lives here, so one file is edited when something changes.
 * Each value says where its evidence is. Nothing here is live data: the live numbers are on the working pages. */

/** Largest fleet that was started and flown in PX4 SITL and Gazebo on the development laptop (docs/FLEET_CAPACITY.md). Planning any number of aircraft is allowed; starting more is limited by TRINETRA_SIM_MAX_AIRCRAFT. */
export const MEASURED_MAX_AIRCRAFT = 6
/** Build label of the front end (frontend/package.json). */
export const BUILD_LABEL = 'LOCAL SIMULATION BUILD 0.1'

export const OVERVIEW = {
  kicker: 'MULTI-UAV GPS-THREAT RESEARCH SIMULATOR',
  descriptor: 'SOFTWARE-ONLY · PX4 SITL AND GAZEBO',
  copy: 'When GPS data turns doubtful, TRINETRA assesses it and suggests a response. The operator decides. Nothing here flies outside the simulator.',
  wordmark: 'SIMULATE. DETECT. RESPOND.',
  heroStatus: ['FIELD MODEL / 00', 'SOFTWARE-ONLY', `TESTED WITH UP TO ${MEASURED_MAX_AIRCRAFT} AIRCRAFT`],
  intro: 'TRINETRA is a software-only research simulator. Aircraft take the roles primary, decoy and standby; a GPS fault is injected at monitor level; the detector reports a probability, and the operator chooses what the fleet does next.',
  deploy: { title: ['Plans you can trust,', 'and check.'], copy: `Plans are versioned and approved before they are deployed, and the fleet starts in stages from the primary's take-off point. You may plan any number of aircraft; this laptop has been measured with up to ${MEASURED_MAX_AIRCRAFT} in the simulator.`, caption: 'VERSIONED PLANS · STAGED START' },
  detect: { title: ['Deception, shown', 'as evidence.'], copy: 'Simulator truth, raw sensors, PX4 estimates and reported GPS are kept apart. The detector compares reported GPS with the aircraft\'s own estimate. Its false-alarm rate of one per hour is a target, not a demonstrated result.', caption: 'REPORTED GPS / ESTIMATE / TRUTH' },
  adapt: { title: ['Responses you', 'confirm.'], copy: 'Resume on track, reassign the task, switch the primary or keep monitoring: each is suggested, never pressed for you. Experiment results are recorded per run and can be exported; improvement is not assumed.', caption: 'ASSESS · SUGGEST · OPERATOR DECIDES' },
  cta: 'Plan a fleet, add a GPS fault to an experiment, watch the threat assessment and the aircraft in Mission Control, then look back at the recorded run and its Excel export.',
  touchdown: 'Touchdown. This is a simulator, and the working pages show backend data. The elevation figure here is scenery.',
}
