# R startup profile mounted only into the frozen v1.1 ichorCNA plotting task.
source("/opt/oncotracer/scripts/ichorcna_plot_compat.R", local = TRUE)
compat <- oncotracer_patch_ichorcna_plot_correction()
oncotracer_write_ichorcna_plot_compat(
  compat,
  file.path(getwd(), ".oncotracer-ichorcna-plot-compat.tsv")
)
