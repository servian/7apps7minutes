include {
  path = find_in_parent_folders()
}

/* Locals ------------------------------------------------------------------- */

locals {
  root_dir = get_parent_terragrunt_dir()
}

/* Dependencies ------------------------------------------------------------- */

dependencies {
  paths = ["${local.root_dir}/project"]
}

dependency "network" {
  config_path = "${local.root_dir}/network"
}

dependency "dns" {
  config_path = "${local.root_dir}/dns"
}

/* Inputs ------------------------------------------------------------------- */

inputs = {
  cloud_dns_zone = dependency.dns.outputs.cloud_dns_zone
  network_name   = dependency.network.outputs.network_name
  subnet_name    = dependency.network.outputs.subnet_name
}
