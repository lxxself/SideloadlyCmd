import plistlib
from pathlib import Path
import unittest
from unittest.mock import patch
from sideloadlycmd.teams import select_team,list_teams
from sideloadlycmd.errors import CliError

class TeamTests(unittest.TestCase):
    def prefs(self,rows):
        return patch.object(Path,'read_bytes',return_value=plistlib.dumps({'DVTDeveloperAccountManagerAppleIDLists':{'group':[{'identifier':'one'}]},'IDEProvisioningTeamByIdentifier':{'one':rows}}))
    def test_unique_personal_wins_over_company(self):
        with self.prefs([{'teamID':'TEAM000001','isFreeProvisioningTeam':True,'teamType':'Personal Team'},{'teamID':'TEAM000002','isFreeProvisioningTeam':False,'teamType':'Company'}]):
            self.assertEqual(select_team(None),('TEAM000001','unique_personal'))
    def test_multiple_personal_teams_require_selection(self):
        with self.prefs([{'teamID':team,'isFreeProvisioningTeam':True} for team in ['TEAM000001','TEAM000002']]):
            with self.assertRaises(CliError): select_team(None)
    def test_explicit_and_offline_never_read_accounts(self):
        with patch.object(Path,'read_bytes') as read:
            self.assertEqual(select_team('TEAM000002',offline=True),('TEAM000002','explicit'))
            with self.assertRaises(CliError): select_team(None,offline=True)
            read.assert_not_called()
    def test_paid_individual_is_not_personal_team(self):
        with self.prefs([{'teamID':'TEAM000001','isFreeProvisioningTeam':False,'teamType':'Individual'}]):
            self.assertFalse(list_teams()[0]['is_personal'])
            self.assertEqual(select_team(None)[1],'unique_available')
    def test_conflicting_metadata_is_not_classified_personal(self):
        with self.prefs([{'teamID':'TEAM000001','isFreeProvisioningTeam':False,'teamType':'Personal Team'}]):
            self.assertEqual(list_teams()[0]['kind'],'unknown')
    def test_shared_personal_team_account_ambiguity_never_falls_back_to_company(self):
        prefs={'DVTDeveloperAccountManagerAppleIDLists':{'group':[{'identifier':'one'},{'identifier':'two'}]},'IDEProvisioningTeamByIdentifier':{'one':[{'teamID':'TEAM000001','isFreeProvisioningTeam':True},{'teamID':'TEAM000002','isFreeProvisioningTeam':False}],'two':[{'teamID':'TEAM000001','isFreeProvisioningTeam':True}]}}
        with patch.object(Path,'read_bytes',return_value=plistlib.dumps(prefs)):
            with self.assertRaises(CliError): select_team(None)
