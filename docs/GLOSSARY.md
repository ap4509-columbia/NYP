# Medical & Clinical Glossary

This file defines every medical term, acronym, procedure name, clinical result code, and guideline reference used in the NYP Women's Health Screening Simulation. Terms are grouped by topic so the glossary doubles as a short primer on the clinical pathways the simulation models.

---

## Screening Guidelines & Authoritative Bodies

| Acronym / Term | Full Name | Role in the simulation |
|---|---|---|
| **USPSTF** | United States Preventive Services Task Force | The federal body that issues evidence-based screening recommendations. The simulation follows USPSTF 2018 (cervical) and USPSTF 2021 (lung) guidelines to determine who is eligible for screening and at what intervals. |
| **ASCCP** | American Society for Colposcopy and Cervical Pathology | Issues risk-based management guidelines for abnormal cervical screening results. The colposcopy result probabilities in `config.py` are intended to be calibrated from ASCCP risk tables. |
| **ACR** | American College of Radiology | Maintains the Lung-RADS reporting system (see below). Also issues guidelines on timely workup for suspicious lung findings. |
| **NCCN** | National Comprehensive Cancer Network | Issues guidelines on lung cancer workup and treatment; referenced in comments for lung biopsy follow-up timing. |
| **CPT** | Current Procedural Terminology | A standardised code set (maintained by the AMA) used to bill for medical procedures. Each procedure in `config.PROCEDURE_REVENUE` is tagged with its CPT code as a calibration reference. |

---

## Cervical Cancer Screening — Tests

| Term | Full Name | What it is |
|---|---|---|
| **Pap smear / Pap test** | Papanicolaou test | The original cervical cancer screening test. Cells are scraped from the cervix and examined under a microscope for abnormalities. In the simulation this is called **cytology**. |
| **Cytology** | Cervical cytology | The laboratory examination of cervical cells. Produces the result categories NORMAL, ASCUS, LSIL, ASC-H, HSIL (see below). USPSTF recommends cytology every **3 years** for women aged 21–65. |
| **HPV test / HPV-alone** | Human Papillomavirus test | Tests cervical cells for the presence of high-risk HPV strains (hrHPV) without looking at cell morphology. Produces only two results: HPV_NEGATIVE or HPV_POSITIVE. USPSTF recommends HPV-alone testing every **5 years** as an alternative to cytology for women aged 30–65. |
| **Co-test / Co-testing** | Combined cytology + HPV test | Running both a Pap smear and an HPV test on the same sample. Previously a USPSTF Grade A recommendation; the simulation uses HPV-alone (not co-testing) as the 5-year option in the base case, consistent with the 2018 update. |
| **hrHPV** | High-risk Human Papillomavirus | The subset of HPV strains (primarily types 16 and 18) that are associated with cervical cancer. The HPV test specifically detects hrHPV. |

---

## Cervical Cytology Result Categories (Bethesda System)

These are the standardised result categories for a Pap smear, defined by the Bethesda System for Reporting Cervical Cytology. The simulation draws results from these categories using age- and risk-stratified probabilities in `config.CERVICAL_RESULT_PROBS`.

| Result code | Full name | Clinical meaning | Simulation routing |
|---|---|---|---|
| **NORMAL** | Normal / Negative for intraepithelial lesion or malignancy | No abnormal cells detected. | Return to routine screening interval. No follow-up action. |
| **ASCUS** | Atypical Squamous Cells of Undetermined Significance | Mildly abnormal cells that are not clearly normal or clearly abnormal. The most common abnormal Pap result. | Referred to colposcopy (20% LTFU before colposcopy). |
| **LSIL** | Low-Grade Squamous Intraepithelial Lesion | Mildly abnormal cells; usually caused by HPV infection. Most resolve on their own. | Referred to colposcopy (20% LTFU). |
| **ASC-H** | Atypical Squamous Cells — Cannot Exclude High-Grade Lesion | Abnormal cells that may represent a high-grade lesion; higher concern than ASCUS. | Referred to colposcopy on an expedited basis (20% LTFU). |
| **HSIL** | High-Grade Squamous Intraepithelial Lesion | Significantly abnormal cells; higher risk of progression to cervical cancer if untreated. | Referred to colposcopy on an expedited basis (20% LTFU). |
| **HPV_NEGATIVE** | HPV test negative | No high-risk HPV detected (HPV-alone test result). | Return to routine 5-year surveillance. |
| **HPV_POSITIVE** | HPV test positive | High-risk HPV detected (HPV-alone test result). Does not mean cancer — most hrHPV infections clear on their own. | 40% repeat in 1 year; 60% referred to colposcopy. |

---

## Colposcopy & CIN Grades

### What is a colposcopy?

A **colposcopy** is a clinical procedure in which a gynaecologist uses a magnifying instrument (the colposcope) to examine the cervix in detail after an abnormal screening result. Acetic acid is applied to the cervix to highlight abnormal areas, and a small tissue sample (biopsy) is taken from any suspicious area. The biopsy result classifies any abnormal tissue into a **CIN grade**.

| Term | Full name | What it means | Simulation routing |
|---|---|---|---|
| **CIN** | Cervical Intraepithelial Neoplasia | Abnormal cell growth on the surface of the cervix. Not cancer — but a precancerous change that may progress to cancer if untreated. Graded 1–3 by severity. | — |
| **CIN1** | CIN Grade 1 | Mild dysplasia. Affects only the lower third of the cervical surface cells. Most CIN1 resolves without treatment. | **Surveillance** — monitoring with repeat screening rather than immediate treatment. |
| **CIN2** | CIN Grade 2 | Moderate dysplasia. Affects the lower two-thirds of the surface cells. Intermediate risk; treatment is typically offered. | **LEEP** excision (with LTFU check before procedure). |
| **CIN3** | CIN Grade 3 | Severe dysplasia / carcinoma in situ. Full thickness of surface cells involved. Highest pre-cancer risk; treatment is strongly recommended. | **LEEP** excision or **cone biopsy** (with LTFU check). |
| **NORMAL** (colposcopy) | No lesion identified | The colposcopy and biopsy find no abnormal tissue despite the abnormal Pap result. | Return to surveillance. |

---

## Cervical Treatment Procedures

| Term | Full name | What it is | CPT code | Simulation revenue (placeholder) |
|---|---|---|---|---|
| **LEEP** | Loop Electrosurgical Excision Procedure | The most common treatment for CIN2 and CIN3. A thin wire loop carrying an electrical current is used to remove the abnormal tissue from the cervix in an outpatient procedure. Also called LLETZ (Large Loop Excision of the Transformation Zone) in some countries. | 57461 | $847 |
| **Cone biopsy** | Cone biopsy / Cold-knife cone (CKC) | Surgical removal of a cone-shaped section of the cervix including the transformation zone. Used when LEEP is insufficient or the lesion extends into the cervical canal. Requires an operating room. | 57520 | $1,240 |
| **Surveillance** | Watchful waiting / Active surveillance | For CIN1 or normal colposcopy: monitoring the patient with repeat screening at defined intervals rather than performing an immediate excisional procedure. No billable procedure in the simulation. | — | $0 |
| **Colposcopy (procedure)** | Colposcopy with biopsy | The colposcopy examination itself, including the cervical biopsy. | 57454 | $312 |

---

## Lung Cancer Screening

### What is LDCT?

**Low-Dose Computed Tomography** (LDCT) of the chest is the only recommended screening test for lung cancer. It uses a very low dose of radiation to produce detailed cross-sectional images of the lungs, allowing radiologists to detect small nodules that may represent early-stage lung cancer. USPSTF 2021 recommends annual LDCT for high-risk individuals (see eligibility criteria below).

| Term | Full name | What it means |
|---|---|---|
| **LDCT** | Low-Dose Computed Tomography | The lung cancer screening test. Annual scan of the chest using reduced radiation. CPT 71271. |
| **CT** | Computed Tomography | Cross-sectional X-ray imaging (the technology underlying LDCT). |
| **Pack-years** | Pack-years of smoking | A measure of cumulative smoking exposure: (packs per day) × (years of smoking). One pack-year = smoking one pack per day for one year. USPSTF requires ≥ 20 pack-years for lung screening eligibility. |
| **Current smoker** | Current tobacco smoker | A person who currently smokes cigarettes. Eligible for lung screening if aged 50–80 and ≥ 20 pack-years. |
| **Former smoker** | Ex-smoker | A person who previously smoked and has quit. Eligible for lung screening only if they quit within the last 15 years (the "quit window"). |
| **Years since quit** | Time since smoking cessation | How long ago the patient stopped smoking. Must be ≤ 15 years for lung screening eligibility under USPSTF 2021. |

---

## Lung-RADS — Reporting Categories

**Lung-RADS** (Lung CT Screening Reporting and Data System) is the standardised system developed by the ACR for reporting LDCT findings. It assigns a category (0–4X) based on the characteristics of any lung nodules found. The simulation draws LDCT results from these categories using the distribution in `config.LUNG_RADS_PROBS`.

| Category | Name | Meaning | Recommended action | Simulation routing |
|---|---|---|---|---|
| **RADS 0** | Incomplete | Scan is uninterpretable or prior comparison scans are needed. | Repeat LDCT in 1–3 months. | Scheduled repeat in 60 days. |
| **RADS 1** | Negative | No nodules or nodules with definitively benign features (e.g., calcified). | Annual routine LDCT. | Rescheduled for 12 months. |
| **RADS 2** | Benign appearance | Nodule(s) with features that are very likely benign. | Annual routine LDCT. | Rescheduled for 12 months. |
| **RADS 3** | Probably benign | A nodule that is probably benign but requires short-interval follow-up. Low risk of malignancy (~1–2%). | Repeat LDCT in 6 months. | Scheduled repeat in 180 days. |
| **RADS 4A** | Suspicious | Nodule(s) with features suspicious for malignancy. Moderate risk (~5–15%). | Tissue sampling (biopsy) or PET-CT. | Enters biopsy pathway (14-day delay). |
| **RADS 4B** | Very suspicious | Larger or more suspicious nodule(s); higher risk of malignancy (>15%). | Biopsy and/or immediate chest oncology referral. | Enters biopsy pathway (14-day delay). |
| **RADS 4X** | Very suspicious with additional features | Category 4 finding with additional imaging features increasing concern for malignancy. | Prompt biopsy and multidisciplinary team review. | Combined with 4B as `RADS_4B_4X` in the simulation. |

---

## Lung Biopsy & Cancer Pathway

| Term | What it is | Simulation use |
|---|---|---|
| **CT-guided needle biopsy** | A biopsy in which a radiologist uses real-time CT imaging to guide a needle into a lung nodule and extract a tissue sample. The standard first-line biopsy for suspicious LDCT findings. CPT 32405. | Triggered for RADS 4A/4B/4X results. 14-day scheduling delay from LDCT. |
| **Malignancy** | Cancer — specifically, tissue confirmed as cancerous by pathological examination of the biopsy sample. | 25% of biopsies confirm malignancy (PLACEHOLDER — calibrate to NYP biopsy yield data). |
| **Benign** | Non-cancerous — the biopsy sample does not contain cancer cells despite a suspicious LDCT appearance. | 75% of biopsies. Patient returns to annual LDCT surveillance. |
| **Lung treatment** | Surgery, radiation, immunotherapy, or medical oncology treatment for confirmed lung cancer. Modelled as a single composite event in the simulation (no sub-pathway). | Triggered when malignancy is confirmed. Revenue placeholder $18,500 (rough composite). |

---

## General Clinical Terms

| Term | Definition |
|---|---|
| **Cervix** | The lower, narrow end of the uterus that connects the uterus to the vagina. The primary anatomical site for cervical cancer screening. |
| **Hysterectomy** | Surgical removal of the uterus. A total hysterectomy also removes the cervix, making the patient ineligible for cervical cancer screening (there is no cervix to screen). The simulation tracks `has_cervix` to enforce this eligibility rule. |
| **HPV** | Human Papillomavirus. A common sexually transmitted viral infection; persistent infection with high-risk strains (hrHPV, especially types 16 and 18) is the primary cause of cervical cancer. Most HPV infections resolve on their own. |
| **HPV vaccination** | Vaccines (Gardasil, Cervarix) that protect against the most cancer-causing HPV strains. Vaccinated patients have a lower probability of an HPV-positive test result in the simulation. |
| **Prior abnormal Pap** | A previous cervical screening result that was not normal (any of: ASCUS, LSIL, ASC-H, or HSIL). History of abnormal results increases the risk-adjusted probability of an abnormal result in future visits. |
| **Prior CIN** | A previous diagnosis of cervical intraepithelial neoplasia (CIN1 or CIN2) from a prior colposcopy. Used in the simulation as a risk factor that inflates the probability of drawing an abnormal result (1.5–1.8× multiplier in `draw_cervical_result()`). |
| **BMI** | Body Mass Index. Ratio of body weight (kg) to height squared (m²). Sampled from a normal distribution (mean 27.5, SD 5.0) in the population module. Not currently used as a screening eligibility criterion in the base model. |
| **Transformation zone** | The area of the cervix where the two cell types meet (squamocolumnar junction). This is where most cervical precancers and cancers develop and where LEEP and cone biopsy tissue is removed from. |
| **Dysplasia** | Abnormal cell growth. In cervical pathology: mild dysplasia = CIN1; moderate = CIN2; severe = CIN3. |
| **Squamous cell** | The flat cells that cover the outer surface of the cervix. Most cervical cancers are squamous cell carcinomas, and the ASCUS / LSIL / ASC-H / HSIL categories all describe squamous cell abnormalities. |
| **Intraepithelial** | Within the epithelium (surface cell layer) but not invasive — has not broken through the basement membrane. CIN is intraepithelial; cervical cancer is invasive. |
| **Carcinoma in situ** | The most severe form of CIN3; full-thickness dysplasia that has not yet invaded surrounding tissue. Treated the same as CIN3. |
| **Surveillance (clinical)** | Active monitoring of a patient with known or suspected disease without immediate treatment. In this simulation: patients with CIN1 or normal colposcopy are placed on surveillance (repeat screening at a defined interval) rather than being treated. |
| **LTFU** | Loss To Follow-Up. A patient who drops out of the care pathway and does not complete a recommended next step (e.g., does not attend a colposcopy after an abnormal Pap, or does not get treatment after CIN3 is diagnosed). LTFU is checked at every clinical decision node in the simulation. |

---

## CPT Codes Reference

CPT (Current Procedural Terminology) codes are used to bill for medical procedures in the US. The simulation uses them as anchors for revenue estimates — actual reimbursement rates must be replaced with NYP contract data.

| CPT Code | Procedure | Placeholder rate |
|---|---|---|
| **88175** | Cervical cytology — liquid-based preparation (ThinPrep) | $156 |
| **87624** | hrHPV nucleic acid detection | $198 |
| **57454** | Colposcopy with directed biopsy and endocervical curettage | $312 |
| **57461** | LEEP (loop electrosurgical excision) | $847 |
| **57520** | Cone biopsy — cold-knife (CKC) | $1,240 |
| **71271** | Low-dose CT thorax for lung cancer screening (LDCT) | $285 |
| **32405** | CT-guided percutaneous needle biopsy of lung | $2,100 |
| *composite* | Lung cancer treatment (surgery / radiation / medical oncology) | $18,500 |

> All rates are **PLACEHOLDER** values based on national average reimbursements. Replace with NYP-specific contract rates before using the simulation for financial planning.

---

## Simulation-Specific Abbreviations

These abbreviations appear in code and output but are specific to this simulation, not standard medical terminology.

| Abbreviation | Meaning |
|---|---|
| **DES** | Discrete-Event Simulation — the modelling technique used. Time advances in discrete steps (days); events (arrivals, appointments, results) happen at specific days. |
| **LTFU** | Loss To Follow-Up (see above — also a standard clinical term). |
| **PCP** | Primary Care Physician — a general practitioner or internist. One of the four provider types in the simulation. |
| **GYN / Gynecologist** | Obstetrician-gynecologist. The provider most associated with cervical screening and pelvic exams. |
| **ER** | Emergency Room / Emergency Department. Drop-in only; no advance scheduling. |
| **RADS** | Short for Lung-RADS category (used in result codes like `RADS_4A`). |
| **pool** | The stable-population pool — the ~15,000 established cycling patients maintained throughout the 70-year simulation. |
| **established patient** | A patient in the cycling pool with an ongoing annual appointment at their primary provider. Distinguished from a first-time visitor or drop-in. |
| **warmup** | The first 365 days of the simulation during which all 15,000 established patients are spread across the schedule so providers start near-capacity from day 1. |
| **pack-years** | See definition above — a measure of cumulative smoking history required for lung screening eligibility. |
| **advance window** | The number of years of future appointments pre-booked per established patient at any time (default: 5 years, `ADVANCE_SCHEDULE_YEARS`). |
| **vacancy filling** | When a deceased patient's future appointment slot is freed up for a new patient. Implemented by counting only active patients when checking outpatient capacity. |
