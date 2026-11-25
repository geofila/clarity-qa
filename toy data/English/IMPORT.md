# Label Studio import

Distribution: 6ef6f091dc68119788fb8cea

Create one project per annotator. Paste labeling_config.xml into Labeling Setup, then import only that annotator's JSON file. Do not import all files into one ordinary Community Edition project: filenames do not enforce task assignment. Community Edition users can see all projects; use separate instances or Enterprise permissions if access must be isolated. Annotators should work independently and must not inspect each other's projects or annotations.

Paste annotation_instructions.html into the project's Instructions field. Each task contains one complete exchange, all numbered sub-questions and their answer summaries. Annotators highlight each entire numbered answer summary and assign one response label, based on the ORIGINAL exchange. Use the Error label for a sub-question absent from the original. The corrections field accepts missing questions as Question: Label and summary-error reports. Summaries and corrections stay in the source language; response labels stay in English.

After annotation, export each project in full JSON format (not JSON_MIN). Save each export under the same annotator filename in a separate exports directory. Then run evaluate-study with the coordinator's study directory and distribution ID. Do not publish this directory as the final research dataset.
