"""Seed list for the first common-med normalization bundle.

This is not a complete drug dictionary. It combines:

* hand-curated high-value aliases for common US/India/EU outpatient medicines,
* current US outpatient anchors from ClinCalc DrugStats Top 300,
* England/EU-style prescribing anchors from OpenPrescribing/NHS chemical names,
* India essential/generic medicine anchors from NLEM/Jan Aushadhi-style coverage,
* a high-signal safety supplement for drugs that appear frequently in our FAERS
  derived source table.

The output remains deterministic: `COMMON_MED_SEEDS` is a tuple of `DrugSeed`
objects consumed by the SQLite normalization builder.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DrugSeed:
    canonical_name: str
    category: str
    aliases: tuple[str, ...]
    region_scope: str = "us/india/eu"


CURATED_COMMON_MED_SEEDS: tuple[DrugSeed, ...] = (
    DrugSeed("acetaminophen", "pain_fever", ("paracetamol", "tylenol", "calpol", "dolo 650")),
    DrugSeed("ibuprofen", "pain_fever", ("advil", "motrin", "brufen")),
    DrugSeed("aspirin", "pain_fever", ("acetylsalicylic acid", "ecosprin", "disprin")),
    DrugSeed("naproxen", "pain_fever", ("aleve", "naprosyn")),
    DrugSeed("diclofenac", "pain_fever", ("voltaren", "voveran")),
    DrugSeed("metformin", "diabetes", ("metformin hydrochloride", "glucophage", "glycomet")),
    DrugSeed("glimepiride", "diabetes", ("amaryl",)),
    DrugSeed("insulin", "diabetes", ("human insulin", "regular insulin")),
    DrugSeed("sitagliptin", "diabetes", ("januvia",)),
    DrugSeed("empagliflozin", "diabetes", ("jardiance",)),
    DrugSeed("amlodipine", "cardiovascular", ("amlodipine besylate", "norvasc", "amlopres")),
    DrugSeed("losartan", "cardiovascular", ("losartan potassium", "cozaar")),
    DrugSeed("telmisartan", "cardiovascular", ("micardis", "telma")),
    DrugSeed("lisinopril", "cardiovascular", ("prinivil", "zestril")),
    DrugSeed("ramipril", "cardiovascular", ("altace", "cardace")),
    DrugSeed("metoprolol", "cardiovascular", ("metoprolol succinate", "metoprolol tartrate", "toprol", "lopressor")),
    DrugSeed("carvedilol", "cardiovascular", ("coreg",)),
    DrugSeed("furosemide", "cardiovascular", ("frusemide", "lasix")),
    DrugSeed("hydrochlorothiazide", "cardiovascular", ("hctz",)),
    DrugSeed("atorvastatin", "cardiovascular", ("lipitor", "atorva")),
    DrugSeed("rosuvastatin", "cardiovascular", ("crestor", "rosuvas")),
    DrugSeed("simvastatin", "cardiovascular", ("zocor",)),
    DrugSeed("pantoprazole", "stomach_acid", ("protonix", "pantocid")),
    DrugSeed("omeprazole", "stomach_acid", ("prilosec", "omez")),
    DrugSeed("esomeprazole", "stomach_acid", ("nexium", "esom")),
    DrugSeed("lansoprazole", "stomach_acid", ("prevacid",)),
    DrugSeed("amoxicillin", "antibiotic", ("amoxycillin", "amoxil")),
    DrugSeed("amoxicillin clavulanate", "antibiotic", ("amoxicillin/clavulanate", "co amoxiclav", "augmentin", "clavam")),
    DrugSeed("azithromycin", "antibiotic", ("zithromax", "azee", "azithral")),
    DrugSeed("ciprofloxacin", "antibiotic", ("cipro", "ciplox")),
    DrugSeed("cefixime", "antibiotic", ("suprax", "taxim o")),
    DrugSeed("cephalexin", "antibiotic", ("cefalexin", "keflex")),
    DrugSeed("sertraline", "psych_neuro", ("zoloft",)),
    DrugSeed("escitalopram", "psych_neuro", ("lexapro", "nexito")),
    DrugSeed("fluoxetine", "psych_neuro", ("prozac", "fludac")),
    DrugSeed("alprazolam", "psych_neuro", ("xanax", "alprax")),
    DrugSeed("clonazepam", "psych_neuro", ("klonopin", "rivotril", "clonotril")),
    DrugSeed("gabapentin", "psych_neuro", ("neurontin",)),
    DrugSeed("pregabalin", "psych_neuro", ("lyrica",)),
    DrugSeed("warfarin", "anticoagulant_antiplatelet", ("coumadin",)),
    DrugSeed("apixaban", "anticoagulant_antiplatelet", ("eliquis",)),
    DrugSeed("rivaroxaban", "anticoagulant_antiplatelet", ("xarelto",)),
    DrugSeed("clopidogrel", "anticoagulant_antiplatelet", ("clopidogrel bisulfate", "plavix")),
    DrugSeed("cetirizine", "allergy_asthma", ("zyrtec", "cetzine")),
    DrugSeed("montelukast", "allergy_asthma", ("singulair", "montek")),
    DrugSeed("albuterol", "allergy_asthma", ("salbutamol", "ventolin", "astalin")),
    DrugSeed("levothyroxine", "thyroid", ("thyroxine", "synthroid", "eltroxin")),
    DrugSeed("prednisone", "steroid", ("deltasone",)),
    DrugSeed("prednisolone", "steroid", ("omnacortil",)),
    DrugSeed("dexamethasone", "steroid", ("decadron", "dexona")),
    DrugSeed("methadone", "pain_opioid", ("methadone hydrochloride",)),
    DrugSeed("fentanyl", "pain_opioid", ("fentanyl citrate",)),
    DrugSeed("buprenorphine", "pain_opioid", ("buprenorphine hydrochloride",)),
    DrugSeed("hydrocodone", "pain_opioid", ("hydrocodone bitartrate",)),
    DrugSeed("hydromorphone", "pain_opioid", ("hydromorphone hydrochloride",)),
    DrugSeed("meperidine", "pain_opioid", ("pethidine", "meperidine hydrochloride", "pethidine hydrochloride")),
    DrugSeed("posaconazole", "antifungal", ("noxafil",)),
    DrugSeed("ritonavir", "antiviral", ("norvir",)),
    DrugSeed("lopinavir ritonavir", "antiviral", ("lopinavir/ritonavir", "kaletra")),
    DrugSeed("cobicistat", "antiviral", ("tybost",)),
    DrugSeed("efavirenz", "antiviral", ("sustiva",)),
    DrugSeed("dronedarone", "cardiovascular", ("multaq",)),
    DrugSeed("sotalol", "cardiovascular", ("sotalol hydrochloride",)),
    DrugSeed("quinidine", "cardiovascular", ("quinidine sulfate", "quinidine gluconate")),
    DrugSeed("dofetilide", "cardiovascular", ("tikosyn",)),
    DrugSeed("procainamide", "cardiovascular", ("procainamide hydrochloride",)),
    DrugSeed("rifampin", "antibiotic", ("rifampicin",)),
    DrugSeed("rifabutin", "antibiotic", ("mycobutin",)),
    DrugSeed("rifapentine", "antibiotic", ("priftin",)),
    DrugSeed("delafloxacin", "antibiotic", ("baxdela",)),
    DrugSeed("norfloxacin", "antibiotic", ("noroxin",)),
    DrugSeed("telithromycin", "antibiotic", ("ketek",)),
    DrugSeed("fluvoxamine", "psych_neuro", ("luvox", "fluvoxamine maleate")),
    DrugSeed("clomipramine", "psych_neuro", ("anafranil", "clomipramine hydrochloride")),
    DrugSeed("imipramine", "psych_neuro", ("tofranil", "imipramine hydrochloride")),
    DrugSeed("triazolam", "psych_neuro", ("halcion",)),
    DrugSeed("temazepam", "psych_neuro", ("restoril",)),
    DrugSeed("eszopiclone", "psych_neuro", ("lunesta",)),
    DrugSeed("zaleplon", "psych_neuro", ("sonata",)),
    DrugSeed("lurasidone", "psych_neuro", ("latuda", "lurasidone hydrochloride")),
    DrugSeed("pimozide", "psych_neuro", ("orap",)),
    DrugSeed("nefazodone", "psych_neuro", ("nefazodone hydrochloride",)),
    DrugSeed("rasagiline", "psych_neuro", ("azilect",)),
    DrugSeed("selegiline", "psych_neuro", ("eldepryl", "zelapar")),
    DrugSeed("moclobemide", "psych_neuro", ()),
    DrugSeed("methylene blue", "other_safety_signal", ("methylthioninium chloride",)),
    DrugSeed("st johns wort", "herbal_supplement", ("st john s wort", "hypericum perforatum")),
    DrugSeed("hydroxyurea", "oncology_immunology", ("hydroxycarbamide",)),
    DrugSeed("fondaparinux", "anticoagulant_antiplatelet", ("fondaparinux sodium", "arixtra")),
    DrugSeed("dalteparin", "anticoagulant_antiplatelet", ("dalteparin sodium", "fragmin")),
    DrugSeed("bivalirudin", "anticoagulant_antiplatelet", ("angiomax",)),
    DrugSeed("argatroban", "anticoagulant_antiplatelet", ()),
    DrugSeed("piroxicam", "pain_fever", ("feldene",)),
    DrugSeed("sulindac", "pain_fever", ("clinoril",)),
    DrugSeed("diflunisal", "pain_fever", ("dolobid",)),
    DrugSeed("ketoprofen", "pain_fever", ("orudis",)),
    DrugSeed("tenoxicam", "pain_fever", ()),
    DrugSeed("nimesulide", "pain_fever", ()),
    DrugSeed("doxylamine", "allergy_asthma", ("doxylamine succinate", "unisom")),
    DrugSeed("quinine", "anti_infective", ("quinine sulfate",)),
    DrugSeed("mefloquine", "anti_infective", ("lariam",)),
    DrugSeed("bedaquiline", "anti_infective", ("sirturo",)),
    DrugSeed("lumefantrine", "anti_infective", ()),
    DrugSeed("azilsartan", "cardiovascular", ("azilsartan medoxomil",)),
    DrugSeed("candesartan", "cardiovascular", ("candesartan cilexetil",)),
    DrugSeed("amiloride", "cardiovascular", ("amiloride hydrochloride",)),
    DrugSeed("triamterene", "cardiovascular", ()),
    DrugSeed("ranolazine", "cardiovascular", ("ranexa",)),
    DrugSeed("vandetanib", "oncology_immunology", ("caprelsa",)),
    DrugSeed("arsenic trioxide", "oncology_immunology", ("trisenox",)),
    DrugSeed("vinblastine", "oncology_immunology", ("vinblastine sulfate",)),
    DrugSeed("fluorouracil", "oncology_immunology", ("5 fluorouracil", "5 fu")),
)


US_CLINCALC_TOP_300_2023 = """
atorvastatin
metformin
levothyroxine
lisinopril
amlodipine
metoprolol
albuterol
losartan
gabapentin
omeprazole
sertraline
rosuvastatin
pantoprazole
escitalopram
amphetamine dextroamphetamine
hydrochlorothiazide
bupropion
fluoxetine
semaglutide
montelukast
trazodone
simvastatin
amoxicillin
tamsulosin
acetaminophen hydrocodone
fluticasone
meloxicam
apixaban
furosemide
insulin glargine
duloxetine
ibuprofen
famotidine
empagliflozin
carvedilol
tramadol
alprazolam
prednisone
hydroxyzine
buspirone
clopidogrel
glipizide
citalopram
potassium chloride
allopurinol
aspirin
cyclobenzaprine
ergocalciferol
oxycodone
methylphenidate
venlafaxine
spironolactone
ondansetron
zolpidem
cetirizine
estradiol
pravastatin
hydrochlorothiazide lisinopril
lamotrigine
quetiapine
fluticasone salmeterol
clonazepam
dulaglutide
azithromycin
hydrochlorothiazide losartan
amoxicillin clavulanate
latanoprost
cholecalciferol
propranolol
ezetimibe
topiramate
paroxetine
diclofenac
budesonide formoterol
atenolol
lisdexamfetamine
doxycycline
pregabalin
ethinyl estradiol norethindrone
glimepiride
tizanidine
clonidine
fenofibrate
insulin lispro
valsartan
cephalexin
baclofen
rivaroxaban
ferrous sulfate
amitriptyline
finasteride
dapagliflozin
acetaminophen oxycodone
folic acid
aripiprazole
olmesartan
ethinyl estradiol norgestimate
valacyclovir
mirtazapine
lorazepam
levetiracetam
insulin aspart
naproxen
cyanocobalamin
loratadine
diltiazem
sumatriptan
triamcinolone
hydralazine
tirzepatide
celecoxib
acetaminophen
alendronate
oxybutynin
hydrochlorothiazide triamterene
warfarin
progesterone
fluticasone umeclidinium vilanterol
testosterone
nifedipine
methocarbamol
benzonatate
sitagliptin
chlorthalidone
isosorbide
donepezil
dexmethylphenidate
sulfamethoxazole trimethoprim
clobetasol
methotrexate
hydroxychloroquine
lovastatin
pioglitazone
irbesartan
methylprednisolone
norethindrone
meclizine
ethinyl estradiol levonorgestrel
fluticasone vilanterol
ketoconazole
thyroid
azelastine
nitrofurantoin
adalimumab
memantine
prednisolone
esomeprazole
docusate
clindamycin
acyclovir
sildenafil
insulin degludec
insulin detemir
drospirenone ethinyl estradiol
ciprofloxacin
morphine
insulin human insulin isophane human
levocetirizine
nirmatrelvir ritonavir
valproate
atomoxetine
budesonide
tiotropium
melatonin
cefdinir
doxepin
olanzapine
phentermine
ofloxacin
ethinyl estradiol etonogestrel
mupirocin
benazepril
timolol
magnesium salts
fluconazole
risperidone
verapamil
linaclotide
cyclosporine
doxazosin
albuterol ipratropium
hydrocortisone
diazepam
telmisartan
carbamazepine
amlodipine benazepril
lithium
evolocumab
desvenlafaxine
dorzolamide
nebivolol
dicyclomine
torsemide
anastrozole
enalapril
polyethylene glycol 3350
tretinoin
tadalafil
sacubitril valsartan
calcium
pramipexole
mesalamine
metronidazole
nortriptyline
emtricitabine tenofovir
rimegepant
nitroglycerin
rizatriptan
liraglutide
acetaminophen codeine
ramipril
ropinirole
brimonidine
mirabegron
colchicine
ticagrelor
terazosin
amiodarone
fexofenadine
liothyronine
bisoprolol
omega 3 acid ethyl esters
flecainide
oxcarbazepine
desogestrel ethinyl estradiol
ascorbic acid
sodium salts
ketorolac
dorzolamide timolol
promethazine
levofloxacin
labetalol
nystatin
cyproheptadine
erythromycin
dutasteride
moxifloxacin
bimatoprost
primidone
sucralfate
betamethasone clotrimazole
senna docusate
bumetanide
icosapent ethyl
solifenacin
dexamethasone
epinephrine
penicillin v
calcitriol
oseltamivir
polymyxin b trimethoprim
dextromethorphan promethazine
terbinafine
linagliptin
methimazole
metoclopramide
medroxyprogesterone
pancrelipase
clotrimazole
dexamethasone neomycin polymyxin b
calcium phosphate cholecalciferol
acetaminophen butalbital caffeine
guanfacine
sodium fluoride
codeine guaifenesin
lactulose
fluorouracil
ipratropium
olopatadine
chlorhexidine
nabumetone
mometasone
hydroquinone
phenazopyridine
loperamide
lidocaine
ciclopirox
cefuroxime
betamethasone
brompheniramine dextromethorphan pseudoephedrine
ethinyl estradiol norgestrel
ciprofloxacin dexamethasone
diphenhydramine
ethinyl estradiol norelgestromin
atropine diphenoxylate
indomethacin
niacin
vitamin e
guaifenesin
pseudoephedrine
bisacodyl
riboflavin
ivermectin
etodolac
lactobacillus acidophilus
tobramycin
ketotifen
loratadine pseudoephedrine
"""


EU_OPENPRESCRIBING_CHEMICAL_ANCHORS = """
aluminium hydroxide
hydrotalcite
magnesium carbonate
co-magaldrox
magnesium oxide
magnesium trisilicate
simeticone
sodium citrate
sodium bicarbonate
calcium carbonate
alverine citrate
atropine sulfate
prucalopride
eluxadoline
belladonna alkaloids
dicycloverine hydrochloride
glycopyrronium bromide
hyoscine butylbromide
mebeverine hydrochloride
peppermint oil
cimetidine
famotidine
nizatidine
ranitidine hydrochloride
sucralfate
misoprostol
rabeprazole sodium
loperamide hydrochloride
racecadotril
telotristat ethyl
mesalazine
olsalazine sodium
balsalazide sodium
sulfasalazine
budesonide
hydrocortisone acetate
hydrocortisone
beclometasone dipropionate
vedolizumab
ustekinumab
risankizumab
sodium cromoglicate
ispaghula husk
methylcellulose
sterculia
bisacodyl
docusate sodium
glycerol
senna
sodium picosulfate
lactitol
lactulose
macrogol 3350
methylnaltrexone bromide
naloxegol
naldemedine
linaclotide
diltiazem hydrochloride
acetarsol
bismuth subgallate
cinchocaine hydrochloride
heparinoid
lidocaine hydrochloride
zinc oxide
fluocortolone
phenol
glyceryl trinitrate
nifedipine
chenodeoxycholic acid
cholic acid
ursodeoxycholic acid
obeticholic acid
elafibranor
pancreatin
digoxin
bendroflumethiazide
chlorothiazide
chlortalidone
indapamide
metolazone
xipamide
bumetanide
torasemide
amiloride hydrochloride
eplerenone
finerenone
mannitol
adenosine
amiodarone hydrochloride
disopyramide
flecainide acetate
mexiletine hydrochloride
propafenone hydrochloride
procainamide hydrochloride
quinidine sulfate
dronedarone hydrochloride
acebutolol hydrochloride
bisoprolol fumarate
labetalol hydrochloride
nadolol
pindolol
propranolol hydrochloride
sotalol hydrochloride
macitentan
riociguat
vericiguat
diazoxide
hydralazine hydrochloride
minoxidil
bosentan
iloprost
ambrisentan
clonidine hydrochloride
guanfacine hydrochloride
methyldopa
moxonidine
doxazosin mesilate
phenoxybenzamine hydrochloride
prazosin hydrochloride
terazosin hydrochloride
captopril
fosinopril sodium
perindopril erbumine
quinapril hydrochloride
trandolapril
olmesartan medoxomil
candesartan cilexetil
eprosartan
aliskiren
isosorbide dinitrate
isosorbide mononitrate
felodipine
lacidipine
lercanidipine hydrochloride
nimodipine
nicardipine hydrochloride
nicorandil
ivabradine
ranolazine
cinnarizine
pentoxifylline
cilostazol
midodrine hydrochloride
ephedrine hydrochloride
adrenaline
fondaparinux sodium
bemiparin sodium
danaparoid sodium
enoxaparin
heparin sodium
dalteparin sodium
tinzaparin sodium
edoxaban
acenocoumarol
phenindione
phenprocoumon
dabigatran etexilate
dipyridamole
prasugrel
alteplase
tranexamic acid
rosuvastatin calcium
alirocumab
bempedoic acid
inclisiran
icosapent
"""


INDIA_ESSENTIAL_AND_GENERIC_ANCHORS = """
aceclofenac
acarbose
acyclovir
albendazole
amikacin
amoxicillin
amoxicillin clavulanate
ampicillin
artesunate
artemether lumefantrine
atenolol
atorvastatin
azithromycin
beclomethasone
benzathine benzylpenicillin
benzyl benzoate
benzylpenicillin
bisoprolol
calamine
calcium carbonate
calcium folinate
captopril
carbamazepine
carbimazole
cefadroxil
cefalexin
cefixime
cefotaxime
cefpodoxime
ceftazidime
ceftriaxone
cefuroxime
cetirizine
chloramphenicol
chloroquine
chlorpheniramine
chlorpromazine
ciprofloxacin
clarithromycin
clobazam
clofazimine
clomifene
clonazepam
clotrimazole
cloxacillin
co-trimoxazole
codeine
cyclophosphamide
cytarabine
dapsone
deferasirox
desloratadine
dexamethasone
dextromethorphan
diazepam
diclofenac
digoxin
diltiazem
dimercaprol
diphenhydramine
dobutamine
domperidone
dopamine
doxycycline
enalapril
enoxaparin
erythromycin
ethambutol
ethinyl estradiol levonorgestrel
etoricoxib
ferrous salt
fluconazole
fluoxetine
folic acid
frusemide
glibenclamide
gliclazide
glimepiride
glipizide
haloperidol
heparin
hydralazine
hydrochlorothiazide
hydrocortisone
hydroxychloroquine
ibuprofen
insulin
isoniazid
isosorbide dinitrate
ivermectin
ketamine
ketoconazole
labetalol
lactulose
lamivudine
levetiracetam
levocetirizine
levodopa carbidopa
levonorgestrel
levothyroxine
lidocaine
linezolid
lithium
loperamide
losartan
magnesium sulfate
mebendazole
mefenamic acid
metformin
methotrexate
methylprednisolone
metoclopramide
metoprolol
metronidazole
midazolam
mifepristone
misoprostol
montelukast
morphine
moxifloxacin
naloxone
naproxen
neomycin
niclosamide
nifedipine
nitrofurantoin
norethisterone
ofloxacin
olanzapine
omeprazole
ondansetron
oral rehydration salts
oseltamivir
oxcarbazepine
oxygen
pantoprazole
paracetamol
penicillin v
phenobarbital
phenytoin
pioglitazone
piperacillin tazobactam
potassium chloride
povidone iodine
praziquantel
prednisolone
prednisone
primaquine
propranolol
pyrazinamide
pyridoxine
rabeprazole
ramipril
ranitidine
rifampicin
risperidone
salbutamol
sertraline
sildenafil
silver sulfadiazine
simvastatin
sitagliptin
sodium bicarbonate
sodium chloride
spironolactone
sucralfate
sulfadoxine pyrimethamine
sulfasalazine
telmisartan
tenofovir
terbinafine
theophylline
thyroxine
tiotropium
tramadol
tranexamic acid
trimethoprim
valacyclovir
valproate
vancomycin
verapamil
vitamin b complex
vitamin c
vitamin d
warfarin
zidovudine
zinc sulfate
"""


FAERS_HIGH_SIGNAL_SAFETY_SUPPLEMENT = """
cyclophosphamide
rituximab
carboplatin
morphine sulfate
tacrolimus
mycophenolate mofetil
paclitaxel
hydromorphone hydrochloride
lenalidomide
fluorouracil
pembrolizumab
oxaliplatin
etoposide
bortezomib
doxorubicin
vincristine
trastuzumab
cisplatin
bevacizumab
tocilizumab
etanercept
cytarabine
daratumumab
tenofovir disoproxil fumarate
docetaxel
leflunomide
infliximab
nivolumab
fludarabine phosphate
capecitabine
azathioprine
atezolizumab
pertuzumab
methadone hydrochloride
secukinumab
letrozole
abatacept
pemetrexed
gemcitabine
ipilimumab
irinotecan
carfilzomib
oxymorphone hydrochloride
pomalidomide
mepolizumab
vancomycin
clozapine
piperacillin tazobactam
omalizumab
venetoclax
melphalan
ifosfamide
leucovorin calcium
fulvestrant
meropenem
ribociclib
human immunoglobulin g
linezolid
zopiclone
propofol
everolimus
busulfan
zoledronic acid
treprostinil
ixazomib
denosumab
tofacitinib
palbociclib
lacosamide
vedolizumab
isatuximab
ruxolitinib
epirubicin
pegaspargase
mercaptopurine
basiliximab
durvalumab
bendamustine
golimumab
thalidomide
ado trastuzumab emtansine
macitentan
ibrutinib
voriconazole
ustekinumab
obinutuzumab
certolizumab pegol
brentuximab vedotin
cetuximab
polatuzumab vedotin
ocrelizumab
lenvatinib
filgrastim
sirolimus
selexipag
amphotericin b
panitumumab
octreotide acetate
azacitidine
leuprolide acetate
daunorubicin
ambrisentan
dacarbazine
alemtuzumab
tucatinib
tislelizumab
tamoxifen
exemestane
vinorelbine tartrate
bleomycin sulfate
valganciclovir
cefepime hydrochloride
daptomycin
temozolomide
upadacitinib
enzalutamide
epcoritamab
dasatinib
anakinra
remdesivir
ganciclovir
abiraterone acetate
darunavir
rocuronium bromide
paliperidone
mesna
encorafenib
apremilast
riociguat
travoprost
imatinib mesylate
mitoxantrone hydrochloride
nintedanib
belimumab
goserelin
cabozantinib
nilotinib
abemaciclib
nevirapine
perampanel
elotuzumab
axicabtagene ciloleucel
icatibant acetate
idarubicin
olaparib
sevoflurane
brivaracetam
baricitinib
eculizumab
selinexor
topotecan hydrochloride
canakinumab
aprepitant
aflibercept
dimethyl fumarate
ponatinib
epoprostenol
belatacept
toripalimab
cenobamate
paliperidone palmitate
acitretin
caspofungin
interferon beta 1a
osimertinib
itraconazole
dalfampridine
cannabidiol
lanadelumab
decitabine
carmustine
dostarlimab
glycopyrrolate
trametinib
ranibizumab
ramucirumab
gilteritinib
ofatumumab
vortioxetine
olmesartan medoxomil
glofitamab
pramipexole
memantine
cladribine
cobimetinib
erythropoietin
teriflunomide
chloroquine
tapentadol
fingolimod
cariprazine
avelumab
cabazitaxel
modafinil
midostaurin
ziprasidone
apalutamide
benralizumab
sorafenib
flucloxacillin
isotretinoin
linagliptin
benztropine
terbinafine
delamanid
atazanavir
vortioxetine
acalabrutinib
isavuconazole
alpelisib
vildagliptin
pirfenidone
terbutaline
crizotinib
bictegravir
alfuzosin
zanubrutinib
pamidronate
guselkumab
erlotinib
mifepristone
glatiramer acetate
canagliflozin
belantamab mafodotin
bilastine
cholestyramine
prochlorperazine
acenocoumarol
theophylline
sunitinib
ivermectin
fosphenytoin
trimethoprim
cabotegravir
spiramycin
ethosuximide
ibandronic acid
aztreonam
lomustine
entecavir
pyridostigmine
rivastigmine
colistin
temsirolimus
lanreotide acetate
pazopanib
ropivacaine
tolvaptan
rifaximin
regorafenib
deferasirox
neratinib
sulpiride
dabigatran
ivacaftor
atracurium
cisatracurium
solifenacin
edoxaban
pimecrolimus
iohexol
"""


DDI_UNRESOLVED_COVERAGE_SUPPLEMENT = """
5 fluorouracil
abacavir
acetazolamide
alimemazine
alogliptin
amiloride
aminocaproic acid
amisulpride
armodafinil
artemether
atovaquone proguanil
atropine
avanafil
azilsartan
balsalazide
bedaquiline
bicalutamide
bismuth subsalicylate
bromazepam
bromocriptine
brotizolam
buprenorphine
cabergoline
candesartan
carisoprodol
chlorambucil
chlordiazepoxide
chlorpropamide
ciclosporin
cidofovir
clomipramine
clotiazepam
cobicistat
colesevelam
colestipol
cyclizine
dalteparin
deflazacort
delafloxacin
dexketoprofen
dexlansoprazole
dihydrocodeine
dihydroergotamine
dimenhydrinate
disulfiram
divalproex sodium
dofetilide
dolutegravir
doravirine
dosulepin
efavirenz
eletriptan
ergotamine
fesoterodine
fluvoxamine
frovatriptan
gemifloxacin
gentamicin
gefitinib
glyburide
granisetron
hyoscyamine
imipramine
insulin nph
isocarboxazid
levomepromazine
levomethadone
levomilnacipran
lormetazepam
lornoxicam
magnesium hydroxide
melperone
methylene blue
milnacipran
minocycline
mitotane
molnupiravir
nalbuphine
naratriptan
netilmicin
niraparib
nitrazepam
orphenadrine
pentazocine
perindopril
pethidine
phenelzine
pheniramine
piritramide
posaconazole
procainamide
quinidine
quinine
rifabutin
roxithromycin
rucaparib
sodium valproate
sotalol
streptomycin
sulfamethoxazole
tedizolid
aluminium magnesium hydroxide
calcium citrate
cilnidipine
febuxostat
formoterol
methylcobalamin
pitavastatin
teneligliptin
tilidine
tolbutamide
tolterodine
tranylcypromine
trihexyphenidyl
trifluoperazine
zuclopenthixol
zolmitriptan
"""


DDI_ALIAS_SUPPLEMENT: dict[str, tuple[str, ...]] = {
    "aspirin": ("aspirin high dose", "aspirin low dose", "low dose aspirin"),
    "cholecalciferol": ("vitamin d3", "vitamin d3 cholecalciferol"),
    "cyclosporine": ("ciclosporin",),
    "fluorouracil": ("5 fluorouracil", "5-fluorouracil"),
    "furosemide": ("frusemide",),
    "insulin": ("insulin nph", "nph insulin", "insulin regular", "regular insulin", "insulin human regular", "human regular insulin"),
    "oral contraceptive": ("combined oral contraceptive pill", "combined oral contraceptive"),
    "sulfamethoxazole trimethoprim": ("co trimoxazole", "cotrimoxazole"),
    "valproate": ("sodium valproate", "valproate sodium"),
}


ANALGESIC_NAMES = {
    "acetaminophen",
    "aceclofenac",
    "aspirin",
    "celecoxib",
    "dexketoprofen",
    "diclofenac",
    "dihydrocodeine",
    "etodolac",
    "etoricoxib",
    "ibuprofen",
    "indomethacin",
    "ketorolac",
    "lornoxicam",
    "meloxicam",
    "morphine",
    "naproxen",
    "oxycodone",
    "tramadol",
}

ANTIBIOTIC_NAMES = {
    "amoxicillin",
    "azithromycin",
    "cefadroxil",
    "cefalexin",
    "cefdinir",
    "cefixime",
    "cefotaxime",
    "cefpodoxime",
    "ceftazidime",
    "ceftriaxone",
    "cefuroxime",
    "cephalexin",
    "ciprofloxacin",
    "clarithromycin",
    "clindamycin",
    "doxycycline",
    "erythromycin",
    "gemifloxacin",
    "gentamicin",
    "levofloxacin",
    "linezolid",
    "metronidazole",
    "moxifloxacin",
    "minocycline",
    "nitrofurantoin",
    "ofloxacin",
    "piperacillin tazobactam",
    "sulfamethoxazole trimethoprim",
    "trimethoprim",
    "vancomycin",
}

CARDIOVASCULAR_TOKENS = {
    "amlodipine",
    "apixaban",
    "atenolol",
    "atorvastatin",
    "bisoprolol",
    "carvedilol",
    "clopidogrel",
    "diltiazem",
    "enalapril",
    "furosemide",
    "hydrochlorothiazide",
    "lisinopril",
    "losartan",
    "metoprolol",
    "nifedipine",
    "ramipril",
    "rivaroxaban",
    "rosuvastatin",
    "simvastatin",
    "telmisartan",
    "valsartan",
    "warfarin",
}

DIABETES_NAMES = {
    "acarbose",
    "dapagliflozin",
    "dulaglutide",
    "empagliflozin",
    "glibenclamide",
    "gliclazide",
    "glimepiride",
    "glipizide",
    "insulin",
    "linagliptin",
    "metformin",
    "pioglitazone",
    "semaglutide",
    "sitagliptin",
    "tirzepatide",
    "vildagliptin",
}

PSYCH_NEURO_NAMES = {
    "alprazolam",
    "amitriptyline",
    "aripiprazole",
    "amisulpride",
    "bupropion",
    "carbamazepine",
    "citalopram",
    "clomipramine",
    "clonazepam",
    "diazepam",
    "duloxetine",
    "escitalopram",
    "fluoxetine",
    "fluvoxamine",
    "gabapentin",
    "lamotrigine",
    "levetiracetam",
    "lithium",
    "lorazepam",
    "mirtazapine",
    "olanzapine",
    "pregabalin",
    "quetiapine",
    "risperidone",
    "sertraline",
    "topiramate",
    "valproate",
    "venlafaxine",
    "zolpidem",
}


def _parse_names(raw: str) -> tuple[str, ...]:
    names = []
    for line in raw.splitlines():
        name = " ".join(line.strip().casefold().replace("/", " ").replace("\\", " ").split())
        if name:
            names.append(name)
    return tuple(names)


def _category_for(name: str) -> str:
    parts = set(name.replace("-", " ").split())
    if name in DIABETES_NAMES or parts & DIABETES_NAMES:
        return "diabetes"
    if name in ANTIBIOTIC_NAMES or parts & ANTIBIOTIC_NAMES:
        return "antibiotic"
    if name in ANALGESIC_NAMES or parts & ANALGESIC_NAMES:
        return "pain_fever"
    if name in PSYCH_NEURO_NAMES or parts & PSYCH_NEURO_NAMES:
        return "psych_neuro"
    if parts & CARDIOVASCULAR_TOKENS:
        return "cardiovascular"
    if any(token in parts for token in ("omeprazole", "pantoprazole", "lansoprazole", "esomeprazole", "rabeprazole", "famotidine")):
        return "stomach_acid"
    if any(token in parts for token in ("cetirizine", "loratadine", "fexofenadine", "albuterol", "salbutamol", "montelukast", "tiotropium")):
        return "allergy_asthma"
    if any(token in parts for token in ("prednisone", "prednisolone", "dexamethasone", "methylprednisolone", "hydrocortisone", "betamethasone")):
        return "steroid"
    if any(token in parts for token in ("cyclophosphamide", "rituximab", "carboplatin", "paclitaxel", "pembrolizumab", "methotrexate")):
        return "oncology_immunology"
    return "regional_common"


def _source_seed(name: str, region_scope: str) -> DrugSeed:
    return DrugSeed(
        canonical_name=name,
        category=_category_for(name),
        aliases=_source_aliases_for(name),
        region_scope=region_scope,
    )


def _source_aliases_for(name: str) -> tuple[str, ...]:
    aliases: set[str] = set()
    if " " in name:
        aliases.add(name.replace(" ", ""))
    for suffix in (
        " hydrochloride",
        " sulfate",
        " sulphate",
        " phosphate",
        " acetate",
        " tartrate",
        " bitartrate",
        " bromide",
        " fumarate",
        " maleate",
        " mesylate",
        " medoxomil",
        " palmitate",
        " sodium",
        " potassium",
        " calcium",
    ):
        if name.endswith(suffix):
            aliases.add(name[: -len(suffix)])
    if "sulfate" in name:
        aliases.add(name.replace("sulfate", "sulphate"))
    if "sulphate" in name:
        aliases.add(name.replace("sulphate", "sulfate"))
    if "aluminum" in name:
        aliases.add(name.replace("aluminum", "aluminium"))
    if "aluminium" in name:
        aliases.add(name.replace("aluminium", "aluminum"))
    return tuple(sorted(alias for alias in aliases if alias != name))


def _build_common_med_seeds() -> tuple[DrugSeed, ...]:
    seeds: list[DrugSeed] = list(CURATED_COMMON_MED_SEEDS)
    seen = {seed.canonical_name for seed in seeds}
    reserved_aliases = {
        alias.casefold()
        for seed in seeds
        for alias in (seed.canonical_name, *seed.aliases)
    }
    source_blocks = (
        (US_CLINCALC_TOP_300_2023, "us"),
        (EU_OPENPRESCRIBING_CHEMICAL_ANCHORS, "eu/uk"),
        (INDIA_ESSENTIAL_AND_GENERIC_ANCHORS, "india"),
        (FAERS_HIGH_SIGNAL_SAFETY_SUPPLEMENT, "faers_high_signal"),
        (DDI_UNRESOLVED_COVERAGE_SUPPLEMENT, "ddi_unresolved_review"),
    )
    for raw, region_scope in source_blocks:
        for name in _parse_names(raw):
            if name not in seen and name not in reserved_aliases:
                seeds.append(_source_seed(name, region_scope))
                seen.add(name)
    for canonical_name, aliases in DDI_ALIAS_SUPPLEMENT.items():
        if canonical_name in seen:
            for idx, seed in enumerate(seeds):
                if seed.canonical_name == canonical_name:
                    merged_aliases = tuple(dict.fromkeys((*seed.aliases, *aliases)))
                    seeds[idx] = DrugSeed(seed.canonical_name, seed.category, merged_aliases, seed.region_scope)
                    break
        else:
            seeds.append(
                DrugSeed(
                    canonical_name=canonical_name,
                    category=_category_for(canonical_name),
                    aliases=aliases,
                    region_scope="ddi_unresolved_review",
                )
            )
            seen.add(canonical_name)
    return tuple(seeds)


COMMON_MED_SEEDS: tuple[DrugSeed, ...] = _build_common_med_seeds()
