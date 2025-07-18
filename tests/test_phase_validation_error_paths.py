"""Tests for phase validation error paths and edge cases."""

import pytest
from span_panel_api.phase_validation import (
    get_tab_phase,
    are_tabs_opposite_phase,
    validate_solar_tabs,
    get_phase_distribution,
    suggest_balanced_pairing,
    get_valid_tabs_from_panel_data,
    get_valid_tabs_from_branches,
)


class TestPhaseValidationErrorPaths:
    """Test error handling and edge cases in phase validation."""

    def test_get_tab_phase_invalid_tab_number(self):
        """Test get_tab_phase with invalid tab numbers."""
        # Tab number outside valid range
        with pytest.raises(ValueError, match="Tab number 0 must be >= 1"):
            get_tab_phase(0)

        # Negative tab number
        with pytest.raises(ValueError, match="Tab number -5 must be >= 1"):
            get_tab_phase(-5)

    def test_get_tab_phase_with_custom_valid_tabs(self):
        """Test get_tab_phase with custom valid tabs list."""
        valid_tabs = [1, 3, 5, 7]  # Only odd numbers

        # Valid tab from the list
        assert get_tab_phase(1, valid_tabs) == "L1"
        assert get_tab_phase(3, valid_tabs) == "L2"

        # Invalid tab not in the list
        with pytest.raises(ValueError, match="Tab number 2 not found in panel branch data"):
            get_tab_phase(2, valid_tabs)

        with pytest.raises(ValueError, match="Tab number 4 not found in panel branch data"):
            get_tab_phase(4, valid_tabs)

    def test_are_tabs_opposite_phase_with_invalid_tabs(self):
        """Test are_tabs_opposite_phase error handling for invalid tabs."""
        # Test line 129-134: ValueError handling in are_tabs_opposite_phase
        # When get_tab_phase raises ValueError, should return False

        # Invalid tab numbers should return False (not raise exception)
        assert are_tabs_opposite_phase(0, 1) is False  # tab 0 invalid
        assert are_tabs_opposite_phase(1, 0) is False  # tab 0 invalid
        assert are_tabs_opposite_phase(-1, 1) is False  # negative tab invalid

        # Both tabs invalid
        assert are_tabs_opposite_phase(0, -1) is False
        assert are_tabs_opposite_phase(-1, -2) is False

        # Test with custom valid_tabs where some tabs are invalid
        valid_tabs = [1, 3, 5]
        assert are_tabs_opposite_phase(1, 2, valid_tabs) is False  # tab 2 not in valid_tabs
        assert are_tabs_opposite_phase(2, 3, valid_tabs) is False  # tab 2 not in valid_tabs

    def test_validate_solar_tabs_same_tab_error(self):
        """Test validate_solar_tabs with same tab numbers."""
        # Test line 153: same tab validation
        valid, message = validate_solar_tabs(5, 5)
        assert valid is False
        assert "Solar tabs cannot be the same tab (5)" in message

        # Test with different tab numbers but same value
        valid, message = validate_solar_tabs(10, 10)
        assert valid is False
        assert "Solar tabs cannot be the same tab (10)" in message

    def test_validate_solar_tabs_same_phase_error(self):
        """Test validate_solar_tabs with tabs on same phase."""
        # Test line 159: same phase validation error
        # Tabs 1 and 2 are both on L1 (position (1-1)//2=0, (2-1)//2=0, both even=L1)
        valid, message = validate_solar_tabs(1, 2)
        assert valid is False
        assert "Solar tabs 1 and 2 are both on L1" in message
        assert "For proper 240V measurement, tabs must be on opposite phases" in message

        # Tabs 3 and 4 are both on L2 (position (3-1)//2=1, (4-1)//2=1, both odd=L2)
        valid, message = validate_solar_tabs(3, 4)
        assert valid is False
        assert "Solar tabs 3 and 4 are both on L2" in message
        assert "For proper 240V measurement, tabs must be on opposite phases" in message

    def test_get_phase_distribution_with_invalid_tabs(self):
        """Test get_phase_distribution with invalid tab numbers."""
        # Test lines 199-200: ValueError handling in get_phase_distribution
        # Mix of valid and invalid tabs - invalid ones should be skipped
        available_tabs = [1, 2, 0, 3, 4, -1, 5]  # Mix valid and invalid

        distribution = get_phase_distribution(available_tabs)

        # Should only include valid tabs (1, 2, 3, 4, 5)
        # Tab phases: 1=L1, 2=L1, 3=L2, 4=L2, 5=L1
        expected_l1 = [1, 2, 5]
        expected_l2 = [3, 4]

        assert distribution["L1_tabs"] == expected_l1
        assert distribution["L2_tabs"] == expected_l2
        assert distribution["L1_count"] == 3
        assert distribution["L2_count"] == 2
        assert distribution["balance_difference"] == 1

    def test_get_phase_distribution_all_invalid_tabs(self):
        """Test get_phase_distribution with all invalid tabs."""
        # Only negative tabs which are invalid
        invalid_tabs = [0, -1, -5, -10]

        distribution = get_phase_distribution(invalid_tabs)

        assert distribution["L1_tabs"] == []
        assert distribution["L2_tabs"] == []
        assert distribution["L1_count"] == 0
        assert distribution["L2_count"] == 0
        assert distribution["balance_difference"] == 0

    def test_suggest_balanced_pairing_function(self):
        """Test suggest_balanced_pairing function."""
        # Test lines 227-238: suggest_balanced_pairing function
        # Test with balanced L1 and L2 tabs
        # Tabs: 1=L1, 2=L1, 3=L2, 4=L2, 5=L1, 6=L1
        available_tabs = [1, 2, 3, 4, 5, 6]

        pairs = suggest_balanced_pairing(available_tabs)

        # Should create pairs from opposite phases
        # L1: [1,2,5,6], L2: [3,4], so pairs will be limited by L2 count
        expected_pairs = [(1, 3), (2, 4)]
        assert pairs == expected_pairs

    def test_suggest_balanced_pairing_unbalanced(self):
        """Test suggest_balanced_pairing with unbalanced phases."""
        # Tabs: 1=L1, 3=L2, 5=L1, 7=L2, 2=L1, 4=L2
        available_tabs = [1, 3, 5, 7, 2, 4]

        pairs = suggest_balanced_pairing(available_tabs)

        # Should only create pairs up to the minimum count
        # L1: [1,5,2], L2: [3,7,4], so we can make 3 pairs
        # Actual order returned: [(1, 3), (2, 4), (5, 7)]
        expected_pairs = [(1, 3), (2, 4), (5, 7)]
        assert pairs == expected_pairs

    def test_suggest_balanced_pairing_empty_input(self):
        """Test suggest_balanced_pairing with empty input."""
        pairs = suggest_balanced_pairing([])
        assert pairs == []

    def test_suggest_balanced_pairing_single_phase_only(self):
        """Test suggest_balanced_pairing with only one phase available."""
        # Only L1 tabs: 1=L1, 2=L1, 5=L1, 6=L1
        l1_only_tabs = [1, 2, 5, 6]
        pairs = suggest_balanced_pairing(l1_only_tabs)
        assert pairs == []  # No L2 tabs, so no pairs possible

        # Only L2 tabs: 3=L2, 4=L2, 7=L2, 8=L2
        l2_only_tabs = [3, 4, 7, 8]
        pairs = suggest_balanced_pairing(l2_only_tabs)
        assert pairs == []  # No L1 tabs, so no pairs possible

    def test_get_valid_tabs_from_panel_data_missing_branches(self):
        """Test get_valid_tabs_from_panel_data with missing branches."""
        # Panel data without branches
        panel_data = {"panel": {"serial_number": "test123"}}

        tabs = get_valid_tabs_from_panel_data(panel_data)
        assert tabs == []

    def test_get_valid_tabs_from_panel_data_empty_branches(self):
        """Test get_valid_tabs_from_panel_data with empty branches."""
        panel_data = {"branches": []}  # Empty list instead of dict

        tabs = get_valid_tabs_from_panel_data(panel_data)
        assert tabs == []

    def test_get_valid_tabs_from_panel_data_branches_not_list(self):
        """Test get_valid_tabs_from_panel_data with branches as dict instead of list."""
        panel_data = {"branches": {}}  # Dict instead of list

        with pytest.raises(TypeError, match="Invalid branches data in panel state"):
            get_valid_tabs_from_panel_data(panel_data)

    def test_get_valid_tabs_from_branches_empty_input(self):
        """Test get_valid_tabs_from_branches with empty branches."""
        tabs = get_valid_tabs_from_branches([])
        assert tabs == []

    def test_get_valid_tabs_from_panel_data_invalid_type(self):
        """Test get_valid_tabs_from_panel_data with invalid panel_state type."""
        # Test line 36: TypeError for invalid panel_state type
        with pytest.raises(TypeError, match="panel_state must be a dictionary"):
            get_valid_tabs_from_panel_data("not_a_dict")

        with pytest.raises(TypeError, match="panel_state must be a dictionary"):
            get_valid_tabs_from_panel_data(None)

        with pytest.raises(TypeError, match="panel_state must be a dictionary"):
            get_valid_tabs_from_panel_data([])

    def test_get_valid_tabs_from_panel_data_branch_validation(self):
        """Test branch validation logic in get_valid_tabs_from_panel_data."""
        # Test lines 44-47: Branch validation logic
        panel_data = {
            "branches": [
                {"id": 1},  # Valid
                {"name": "no_id"},  # Missing id
                {"id": "not_int"},  # Non-integer id
                {"id": 0},  # Zero id
                {"id": -1},  # Negative id
                "not_dict",  # Not a dict
                {"id": 5},  # Valid
            ]
        }

        tabs = get_valid_tabs_from_panel_data(panel_data)
        assert tabs == [1, 5]  # Only valid tabs should be included

    def test_get_valid_tabs_from_branches_object_handling(self):
        """Test get_valid_tabs_from_branches with Branch objects."""
        # Test lines 65-73: Branch object handling with hasattr

        # Mock Branch object
        class MockBranch:
            def __init__(self, tab_id):
                self.id = tab_id

        # Test with Branch objects
        branches = [
            MockBranch(1),  # Valid object
            MockBranch("not_int"),  # Invalid object
            MockBranch(0),  # Invalid object
            MockBranch(-1),  # Invalid object
            MockBranch(5),  # Valid object
        ]

        tabs = get_valid_tabs_from_branches(branches)
        assert tabs == [1, 5]  # Only valid tabs should be included

    def test_get_valid_tabs_from_branches_mixed_types(self):
        """Test get_valid_tabs_from_branches with mixed object and dict types."""

        # Test mixed Branch objects and dictionaries
        class MockBranch:
            def __init__(self, tab_id):
                self.id = tab_id

        branches = [
            MockBranch(1),  # Valid object
            {"id": 2},  # Valid dict
            MockBranch("not_int"),  # Invalid object
            {"name": "no_id"},  # Invalid dict
            MockBranch(3),  # Valid object
            {"id": 4},  # Valid dict
        ]

        tabs = get_valid_tabs_from_branches(branches)
        assert tabs == [1, 2, 3, 4]  # All valid tabs should be included

    def test_validate_solar_tabs_value_error_handling(self):
        """Test validate_solar_tabs ValueError exception handling."""
        # Test line 132: ValueError exception in validate_solar_tabs
        # Test with invalid tab numbers
        valid, message = validate_solar_tabs(0, 1)
        assert valid is False
        assert "Invalid tab configuration" in message

        valid, message = validate_solar_tabs(1, 0)
        assert valid is False
        assert "Invalid tab configuration" in message

        # Test with custom valid_tabs where one tab is invalid
        valid_tabs = [1, 3, 5]
        valid, message = validate_solar_tabs(1, 2, valid_tabs)
        assert valid is False
        assert "Invalid tab configuration" in message

        # Test with both tabs invalid but different to ensure ValueError is caught
        valid, message = validate_solar_tabs(0, -1, valid_tabs)
        assert valid is False
        assert "Invalid tab configuration" in message

    def test_get_phase_distribution_balance_calculation(self):
        """Test get_phase_distribution balance calculation logic."""
        # Test lines 166-167: Balance calculation logic

        # Test perfectly balanced
        tabs = [1, 2, 3, 4]  # L1: [1,2], L2: [3,4]
        distribution = get_phase_distribution(tabs)
        assert distribution["is_balanced"] is True
        assert distribution["balance_difference"] == 0

        # Test slightly unbalanced (difference of 1)
        tabs = [1, 2, 3]  # L1: [1,2], L2: [3]
        distribution = get_phase_distribution(tabs)
        assert distribution["is_balanced"] is True
        assert distribution["balance_difference"] == 1

        # Test more unbalanced (difference of 2)
        tabs = [1, 2, 3, 4, 5, 6]  # L1: [1,2,5,6], L2: [3,4]
        distribution = get_phase_distribution(tabs)
        assert distribution["is_balanced"] is False
        assert distribution["balance_difference"] == 2
