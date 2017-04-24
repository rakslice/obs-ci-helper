# obs-ci-helper #

This is a script to take build results (success/failure/error) from an [openbuildservice](http://openbuildservice.org "openbuildservice") / [build.opensuse.org](https://build.opensuse.org/) build job for a `git` branch and put them onto the corresponding commits in [GitHub](https://github.com/).

It is for python 2.7.

This isn't a ready-for-prime-time quality CI solution, but if you really need to use OBS for whatever reason you might be able to do some useful things with it. 

**Setup**

1. Configure your build job with a `_service` that pulls from a git branch and puts the full 40-digit git revision into the version number before the final `-` (see [&#95;service&#95;example](_service_example) for an example; there is more discussion in [the old OBS wiki docs](https://en.opensuse.org/openSUSE:Build_Service_Concept_SourceService#Example_2:_GIT_integration)).
2. Create a `settings.json` file, using [settings.example.json](settings.example.json) as a template.  Enter your OBS project details and login info, and GitHub account and project name.
3. Create a GitHub login token for this script to use
	1. Go to your [GitHub account settings' Personal access tokens page](https://github.com/settings/tokens)
	2. Click on Generate token
	3. Under **Select scopes**, check the check box next to **repo:status**
	4. Enter a memorable **Token description**
	5. Click on **Generate Token**
	6. Copy the displayed token in your `settings.json`. Don't forget to save.

You can also [set up your build job to be triggered by GitHub with an OBS token](http://openbuildservice.org/2013/11/22/Source-Update-Via_Token/), if you want builds to happen automatically when you push to the branch, also that's not required for this script.

