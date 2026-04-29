#!/usr/bin/env python3
"""Tests for infer_backend_params.py.

The script now consumes the output of `mvn help:effective-pom`, so test
fixtures are <project> elements with all inheritance and properties
already resolved (no ${...} references, no <modules> walking).
"""

import os
import sys
import textwrap
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import infer_backend_params as infer
from utils import strip_ns


def parse_project(xml_str):
    """Parse a <project>...</project> XML string into a stripped element."""
    root = ET.fromstring(textwrap.dedent(xml_str))
    strip_ns(root)
    return root


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------

class TestParseVersion(unittest.TestCase):
    def test_three_part(self):
        self.assertEqual(infer.parse_version("2.6.1"), (2, 6, 1))

    def test_snapshot(self):
        self.assertEqual(infer.parse_version("3.0.0-SNAPSHOT"), (3, 0, 0))

    def test_two_part(self):
        self.assertEqual(infer.parse_version("2.7"), (2, 7, 0))

    def test_one_part(self):
        self.assertEqual(infer.parse_version("8"), (8, 0, 0))

    def test_qualifier(self):
        self.assertEqual(infer.parse_version("2.4.0-alpha1"), (2, 4, 0))

    def test_build_metadata(self):
        self.assertEqual(infer.parse_version("2.8.0+build.123"), (2, 8, 0))


class TestNormalizeJava(unittest.TestCase):
    def test_old_style_1_8(self):
        self.assertEqual(infer.normalize_java("1.8"), "8")

    def test_old_style_1_7(self):
        self.assertEqual(infer.normalize_java("1.7"), "7")

    def test_modern_style_11(self):
        self.assertEqual(infer.normalize_java("11"), "11")

    def test_modern_style_21(self):
        self.assertEqual(infer.normalize_java("21"), "21")

    def test_whitespace(self):
        self.assertEqual(infer.normalize_java("  1.8  "), "8")


# ---------------------------------------------------------------------------
# OpenMRS version → Java mapping
# ---------------------------------------------------------------------------

class TestMapToJava(unittest.TestCase):
    def test_2_0_0(self):
        self.assertEqual(infer.map_to_java("2.0.0"), [8])

    def test_2_3_9(self):
        self.assertEqual(infer.map_to_java("2.3.9"), [8])

    def test_2_4_0(self):
        self.assertEqual(infer.map_to_java("2.4.0"), [8, 11])

    def test_2_6_1(self):
        self.assertEqual(infer.map_to_java("2.6.1"), [8, 11])

    def test_2_7_0(self):
        self.assertEqual(infer.map_to_java("2.7.0"), [8, 11, 17, 21])

    def test_2_7_4(self):
        self.assertEqual(infer.map_to_java("2.7.4"), [8, 11, 17, 21])

    def test_2_8_0(self):
        self.assertEqual(infer.map_to_java("2.8.0"), [8, 11, 17, 21])

    def test_2_8_5(self):
        self.assertEqual(infer.map_to_java("2.8.5"), [8, 11, 17, 21])

    def test_3_0_0(self):
        self.assertEqual(infer.map_to_java("3.0.0"), [25])

    def test_3_0_0_snapshot(self):
        self.assertEqual(infer.map_to_java("3.0.0-SNAPSHOT"), [25])

    def test_below_2_0(self):
        self.assertIsNone(infer.map_to_java("1.12.0"))


class TestMapRangeToJava(unittest.TestCase):
    def test_single_tier(self):
        self.assertEqual(infer.map_range_to_java("[2.4.0, 2.7.0)"), [8, 11])

    def test_spanning_two_tiers(self):
        self.assertEqual(infer.map_range_to_java("[2.6.0, 2.8.0)"), [8, 11, 17, 21])

    def test_spanning_many_tiers(self):
        self.assertEqual(infer.map_range_to_java("[2.4.0, 3.0.0)"), [8, 11, 17, 21])

    def test_unbounded_upper(self):
        result = infer.map_range_to_java("[2.7.0,)")
        self.assertEqual(result, [8, 11, 17, 21, 25])

    def test_3_x_range(self):
        self.assertEqual(infer.map_range_to_java("[3.0.0,)"), [25])


# ---------------------------------------------------------------------------
# Compiler version detection
# ---------------------------------------------------------------------------

class TestFindCompilerVersion(unittest.TestCase):
    def _project(self, xml_str):
        proj = parse_project(xml_str)
        return proj, infer.get_properties(proj)

    def test_compiler_release_property(self):
        proj, props = self._project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <maven.compiler.release>11</maven.compiler.release>
                <maven.compiler.target>8</maven.compiler.target>
              </properties>
            </project>""")
        self.assertEqual(infer.find_compiler_version(proj, props), "11")

    def test_compiler_target_property(self):
        proj, props = self._project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <maven.compiler.target>1.8</maven.compiler.target>
              </properties>
            </project>""")
        self.assertEqual(infer.find_compiler_version(proj, props), "8")

    def test_plugin_config_target(self):
        proj, props = self._project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <build>
                <pluginManagement>
                  <plugins>
                    <plugin>
                      <artifactId>maven-compiler-plugin</artifactId>
                      <configuration>
                        <target>1.8</target>
                      </configuration>
                    </plugin>
                  </plugins>
                </pluginManagement>
              </build>
            </project>""")
        self.assertEqual(infer.find_compiler_version(proj, props), "8")

    def test_plugin_config_release(self):
        proj, props = self._project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <build>
                <plugins>
                  <plugin>
                    <artifactId>maven-compiler-plugin</artifactId>
                    <configuration>
                      <release>17</release>
                    </configuration>
                  </plugin>
                </plugins>
              </build>
            </project>""")
        self.assertEqual(infer.find_compiler_version(proj, props), "17")

    def test_no_compiler_settings(self):
        proj, props = self._project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <someOtherProp>value</someOtherProp>
              </properties>
            </project>""")
        self.assertIsNone(infer.find_compiler_version(proj, props))

    def test_no_namespace(self):
        proj, props = self._project("""\
            <project>
              <properties>
                <maven.compiler.target>11</maven.compiler.target>
              </properties>
            </project>""")
        self.assertEqual(infer.find_compiler_version(proj, props), "11")

    def test_unresolved_property_in_property_ignored(self):
        proj, props = self._project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <maven.compiler.target>${javaVersion}</maven.compiler.target>
              </properties>
            </project>""")
        self.assertIsNone(infer.find_compiler_version(proj, props))

    def test_unresolved_property_in_plugin_target_ignored(self):
        proj, props = self._project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <build>
                <plugins>
                  <plugin>
                    <artifactId>maven-compiler-plugin</artifactId>
                    <configuration>
                      <target>${javaVersion}</target>
                    </configuration>
                  </plugin>
                </plugins>
              </build>
            </project>""")
        self.assertIsNone(infer.find_compiler_version(proj, props))

    def test_unresolved_property_skipped_for_resolved_fallback(self):
        proj, props = self._project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <maven.compiler.target>${javaVersion}</maven.compiler.target>
              </properties>
              <build>
                <plugins>
                  <plugin>
                    <artifactId>maven-compiler-plugin</artifactId>
                    <configuration>
                      <target>17</target>
                    </configuration>
                  </plugin>
                </plugins>
              </build>
            </project>""")
        self.assertEqual(infer.find_compiler_version(proj, props), "17")


# ---------------------------------------------------------------------------
# OpenMRS version detection
# ---------------------------------------------------------------------------

class TestFindOpenmrsVersion(unittest.TestCase):
    def _projects(self, *xml_strs):
        return [parse_project(s) for s in xml_strs]

    def test_dependency_management(self):
        # Effective POM has properties already resolved.
        projects = self._projects("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <dependencyManagement>
                <dependencies>
                  <dependency>
                    <groupId>org.openmrs.api</groupId>
                    <artifactId>openmrs-api</artifactId>
                    <version>2.6.1</version>
                  </dependency>
                </dependencies>
              </dependencyManagement>
            </project>""")
        self.assertEqual(infer.find_openmrs_version(projects), "2.6.1")

    def test_direct_dependency(self):
        projects = self._projects("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <dependencies>
                <dependency>
                  <groupId>org.openmrs.api</groupId>
                  <artifactId>openmrs-api</artifactId>
                  <version>2.8.0</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertEqual(infer.find_openmrs_version(projects), "2.8.0")

    def test_dependency_only_in_submodule(self):
        # Aggregator declares no OpenMRS dep; submodule does.
        projects = self._projects(
            """\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <artifactId>aggregator</artifactId>
              <modules>
                <module>api</module>
              </modules>
            </project>""",
            """\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <artifactId>aggregator-api</artifactId>
              <dependencies>
                <dependency>
                  <groupId>org.openmrs.api</groupId>
                  <artifactId>openmrs-api</artifactId>
                  <version>2.7.4</version>
                </dependency>
              </dependencies>
            </project>""",
        )
        self.assertEqual(infer.find_openmrs_version(projects), "2.7.4")

    def test_openmrs_web(self):
        projects = self._projects("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <dependencies>
                <dependency>
                  <groupId>org.openmrs.web</groupId>
                  <artifactId>openmrs-web</artifactId>
                  <version>2.4.0</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertEqual(infer.find_openmrs_version(projects), "2.4.0")

    def test_no_openmrs_dep(self):
        projects = self._projects("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <dependencies>
                <dependency>
                  <groupId>junit</groupId>
                  <artifactId>junit</artifactId>
                  <version>4.13</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertIsNone(infer.find_openmrs_version(projects))

    def test_platform_version_property_fallback(self):
        # No OpenMRS dep declared, but the property is set (in practice from
        # the inherited contrib parent POM).
        projects = self._projects("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <openmrsPlatformVersion>2.6.1</openmrsPlatformVersion>
              </properties>
            </project>""")
        self.assertEqual(infer.find_openmrs_version(projects), "2.6.1")

    def test_dependencies_take_priority_over_dependency_management(self):
        # When both are present with different versions (a submodule
        # overriding inherited dependencyManagement), the actual <dependencies>
        # version wins — that's what the build actually compiles against.
        projects = self._projects("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <dependencyManagement>
                <dependencies>
                  <dependency>
                    <groupId>org.openmrs.api</groupId>
                    <artifactId>openmrs-api</artifactId>
                    <version>2.8.0</version>
                  </dependency>
                </dependencies>
              </dependencyManagement>
              <dependencies>
                <dependency>
                  <groupId>org.openmrs.api</groupId>
                  <artifactId>openmrs-api</artifactId>
                  <version>2.6.1</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertEqual(infer.find_openmrs_version(projects), "2.6.1")

    def test_submodule_dependency_beats_aggregator_dependency_management(self):
        # Multi-project: aggregator declares 2.8.0 in dependencyManagement,
        # but a submodule actually depends on 2.7.4. The submodule's actual
        # version should win.
        projects = self._projects(
            """\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <artifactId>aggregator</artifactId>
              <dependencyManagement>
                <dependencies>
                  <dependency>
                    <groupId>org.openmrs.api</groupId>
                    <artifactId>openmrs-api</artifactId>
                    <version>2.8.0</version>
                  </dependency>
                </dependencies>
              </dependencyManagement>
            </project>""",
            """\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <artifactId>aggregator-api</artifactId>
              <dependencies>
                <dependency>
                  <groupId>org.openmrs.api</groupId>
                  <artifactId>openmrs-api</artifactId>
                  <version>2.7.4</version>
                </dependency>
              </dependencies>
            </project>""",
        )
        self.assertEqual(infer.find_openmrs_version(projects), "2.7.4")

    def test_explicit_dep_takes_priority_over_property(self):
        # If both an OpenMRS dep and the platform property exist, the dep wins.
        projects = self._projects("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <openmrsPlatformVersion>9.9.9</openmrsPlatformVersion>
              </properties>
              <dependencyManagement>
                <dependencies>
                  <dependency>
                    <groupId>org.openmrs.api</groupId>
                    <artifactId>openmrs-api</artifactId>
                    <version>2.8.0</version>
                  </dependency>
                </dependencies>
              </dependencyManagement>
            </project>""")
        self.assertEqual(infer.find_openmrs_version(projects), "2.8.0")

    def test_version_range(self):
        projects = self._projects("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <dependencyManagement>
                <dependencies>
                  <dependency>
                    <groupId>org.openmrs.api</groupId>
                    <artifactId>openmrs-api</artifactId>
                    <version>[2.4.0, 2.7.0)</version>
                  </dependency>
                </dependencies>
              </dependencyManagement>
            </project>""")
        self.assertEqual(infer.find_openmrs_version(projects), "[2.4.0, 2.7.0)")


# ---------------------------------------------------------------------------
# Maven server IDs
# ---------------------------------------------------------------------------

class TestFindServerIds(unittest.TestCase):
    def test_both_ids(self):
        proj = parse_project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <distributionManagement>
                <repository>
                  <id>openmrs-repo-modules</id>
                  <url>https://example.com/releases</url>
                </repository>
                <snapshotRepository>
                  <id>openmrs-repo-snapshots</id>
                  <url>https://example.com/snapshots</url>
                </snapshotRepository>
              </distributionManagement>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(proj)
        self.assertEqual(release_id, "openmrs-repo-modules")
        self.assertEqual(snapshot_id, "openmrs-repo-snapshots")

    def test_only_release(self):
        proj = parse_project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <distributionManagement>
                <repository>
                  <id>my-releases</id>
                </repository>
              </distributionManagement>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(proj)
        self.assertEqual(release_id, "my-releases")
        self.assertIsNone(snapshot_id)

    def test_only_snapshot(self):
        proj = parse_project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <distributionManagement>
                <snapshotRepository>
                  <id>my-snapshots</id>
                </snapshotRepository>
              </distributionManagement>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(proj)
        self.assertIsNone(release_id)
        self.assertEqual(snapshot_id, "my-snapshots")

    def test_no_distribution_management(self):
        proj = parse_project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <groupId>org.example</groupId>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(proj)
        self.assertIsNone(release_id)
        self.assertIsNone(snapshot_id)

    def test_no_namespace(self):
        proj = parse_project("""\
            <project>
              <distributionManagement>
                <repository>
                  <id>releases</id>
                </repository>
                <snapshotRepository>
                  <id>snapshots</id>
                </snapshotRepository>
              </distributionManagement>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(proj)
        self.assertEqual(release_id, "releases")
        self.assertEqual(snapshot_id, "snapshots")

    def test_empty_ids(self):
        proj = parse_project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <distributionManagement>
                <repository>
                  <id></id>
                </repository>
                <snapshotRepository>
                  <id></id>
                </snapshotRepository>
              </distributionManagement>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(proj)
        self.assertIsNone(release_id)
        self.assertIsNone(snapshot_id)


# ---------------------------------------------------------------------------
# POM project version
# ---------------------------------------------------------------------------

class TestFindProjectVersion(unittest.TestCase):
    def test_direct_version(self):
        proj = parse_project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <version>1.2.3</version>
            </project>""")
        self.assertEqual(infer.find_project_version(proj), "1.2.3")

    def test_snapshot_version(self):
        proj = parse_project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <version>2.0.0-SNAPSHOT</version>
            </project>""")
        self.assertEqual(infer.find_project_version(proj), "2.0.0-SNAPSHOT")

    def test_parent_version_fallback(self):
        proj = parse_project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <parent>
                <version>3.1.0</version>
              </parent>
            </project>""")
        self.assertEqual(infer.find_project_version(proj), "3.1.0")

    def test_direct_version_takes_precedence(self):
        proj = parse_project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <version>1.0.0</version>
              <parent>
                <version>2.0.0</version>
              </parent>
            </project>""")
        self.assertEqual(infer.find_project_version(proj), "1.0.0")

    def test_no_version(self):
        proj = parse_project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <groupId>org.example</groupId>
            </project>""")
        self.assertIsNone(infer.find_project_version(proj))

    def test_whitespace_stripped(self):
        proj = parse_project("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <version>  1.0.0  </version>
            </project>""")
        self.assertEqual(infer.find_project_version(proj), "1.0.0")

    def test_no_namespace(self):
        proj = parse_project("""\
            <project>
              <version>4.5.6</version>
            </project>""")
        self.assertEqual(infer.find_project_version(proj), "4.5.6")


# ---------------------------------------------------------------------------
# Effective POM loading
# ---------------------------------------------------------------------------

class TestLoadProjects(unittest.TestCase):
    def _write(self, tmpdir, xml):
        path = os.path.join(tmpdir, "effective-pom.xml")
        with open(path, "w") as f:
            f.write(textwrap.dedent(xml))
        return path

    def test_single_project_root(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, """\
                <project xmlns="http://maven.apache.org/POM/4.0.0">
                  <artifactId>only</artifactId>
                </project>""")
            projects = infer.load_projects(path)
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0].findtext("artifactId"), "only")

    def test_multi_project_wrapper(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, """\
                <projects>
                  <project xmlns="http://maven.apache.org/POM/4.0.0">
                    <artifactId>root</artifactId>
                  </project>
                  <project xmlns="http://maven.apache.org/POM/4.0.0">
                    <artifactId>api</artifactId>
                  </project>
                </projects>""")
            projects = infer.load_projects(path)
            self.assertEqual(len(projects), 2)
            self.assertEqual(projects[0].findtext("artifactId"), "root")
            self.assertEqual(projects[1].findtext("artifactId"), "api")

    def test_missing_file_exits(self):
        with self.assertRaises(SystemExit):
            infer.load_projects("/tmp/nonexistent-effective-pom-xyz.xml")


# ---------------------------------------------------------------------------
# End-to-end integration tests
# ---------------------------------------------------------------------------

class TestEndToEnd(unittest.TestCase):
    """Integration tests that run the inference pipeline against effective POMs."""

    def _run_inference(self, *project_xmls):
        projects = [parse_project(s) for s in project_xmls]
        primary = projects[0]
        props = infer.get_properties(primary)
        compiler_version = infer.find_compiler_version(primary, props)
        openmrs_ver = infer.find_openmrs_version(projects)
        java_versions = infer.map_to_java(openmrs_ver) if openmrs_ver else None

        if compiler_version and java_versions:
            compiler_int = int(compiler_version)
            filtered = [v for v in java_versions if v >= compiler_int]
            java_versions = filtered if filtered else [compiler_int]

        if java_versions:
            main_java = str(min(java_versions))
        elif compiler_version:
            main_java = compiler_version
        else:
            main_java = None

        return main_java, java_versions

    def test_typical_module_target_1_8_openmrs_2_6(self):
        main, versions = self._run_inference("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <maven.compiler.target>1.8</maven.compiler.target>
                <openmrsPlatformVersion>2.6.1</openmrsPlatformVersion>
              </properties>
              <dependencyManagement>
                <dependencies>
                  <dependency>
                    <groupId>org.openmrs.api</groupId>
                    <artifactId>openmrs-api</artifactId>
                    <version>2.6.1</version>
                  </dependency>
                </dependencies>
              </dependencyManagement>
            </project>""")
        self.assertEqual(main, "8")
        self.assertEqual(versions, [8, 11])

    def test_compiler_below_3_x_floor_clamped(self):
        main, versions = self._run_inference("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <dependencyManagement>
                <dependencies>
                  <dependency>
                    <groupId>org.openmrs.api</groupId>
                    <artifactId>openmrs-api</artifactId>
                    <version>3.0.0-SNAPSHOT</version>
                  </dependency>
                </dependencies>
              </dependencyManagement>
              <build>
                <plugins>
                  <plugin>
                    <artifactId>maven-compiler-plugin</artifactId>
                    <configuration><target>21</target></configuration>
                  </plugin>
                </plugins>
              </build>
            </project>""")
        self.assertEqual(main, "25")
        self.assertEqual(versions, [25])

    def test_no_compiler_settings_falls_back_to_min(self):
        main, versions = self._run_inference("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <dependencies>
                <dependency>
                  <groupId>org.openmrs.api</groupId>
                  <artifactId>openmrs-api</artifactId>
                  <version>2.8.5</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertEqual(main, "8")
        self.assertEqual(versions, [8, 11, 17, 21])

    def test_compiler_filters_lower_java_versions(self):
        """Java versions below the compiler requirement are excluded from the matrix."""
        main, versions = self._run_inference("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <maven.compiler.release>11</maven.compiler.release>
                <maven.compiler.target>8</maven.compiler.target>
              </properties>
              <dependencies>
                <dependency>
                  <groupId>org.openmrs.api</groupId>
                  <artifactId>openmrs-api</artifactId>
                  <version>2.7.0</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertEqual(main, "11")
        self.assertEqual(versions, [11, 17, 21])

    def test_compiler_above_matrix_replaces_matrix(self):
        main, versions = self._run_inference("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <build>
                <plugins>
                  <plugin>
                    <artifactId>maven-compiler-plugin</artifactId>
                    <configuration><target>21</target></configuration>
                  </plugin>
                </plugins>
              </build>
              <dependencies>
                <dependency>
                  <groupId>org.openmrs.api</groupId>
                  <artifactId>openmrs-api</artifactId>
                  <version>2.4.0</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertEqual(main, "21")
        self.assertEqual(versions, [21])

    def test_compiler_below_matrix_floor_clamped(self):
        # Real-world case (initializer): compiler target 1.6 with OpenMRS 2.1
        # matrix [8]. JVM 8+ can run target=1.6 bytecode, and the OpenMRS
        # platform itself can't run on Java 6, so the matrix wins.
        main, versions = self._run_inference("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <maven.compiler.target>1.6</maven.compiler.target>
              </properties>
              <dependencies>
                <dependency>
                  <groupId>org.openmrs.api</groupId>
                  <artifactId>openmrs-api</artifactId>
                  <version>2.1.1</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertEqual(main, "8")
        self.assertEqual(versions, [8])

    def test_unresolved_compiler_property_falls_through(self):
        main, versions = self._run_inference("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <openmrsPlatformVersion>1.11.6</openmrsPlatformVersion>
              </properties>
              <build>
                <plugins>
                  <plugin>
                    <artifactId>maven-compiler-plugin</artifactId>
                    <configuration>
                      <source>${javaVersion}</source>
                      <target>${javaVersion}</target>
                    </configuration>
                  </plugin>
                </plugins>
              </build>
            </project>""")
        self.assertIsNone(main)
        self.assertIsNone(versions)

    def test_version_range_union(self):
        main, versions = self._run_inference("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <maven.compiler.target>1.8</maven.compiler.target>
              </properties>
              <dependencyManagement>
                <dependencies>
                  <dependency>
                    <groupId>org.openmrs.api</groupId>
                    <artifactId>openmrs-api</artifactId>
                    <version>[2.6.0, 2.8.0)</version>
                  </dependency>
                </dependencies>
              </dependencyManagement>
            </project>""")
        self.assertEqual(main, "8")
        self.assertEqual(versions, [8, 11, 17, 21])

    def test_dep_in_submodule_only(self):
        # Multi-module: aggregator has no OpenMRS dep, submodule does.
        # Compiler settings come from the aggregator.
        main, versions = self._run_inference(
            """\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <maven.compiler.target>8</maven.compiler.target>
              </properties>
              <artifactId>aggregator</artifactId>
            </project>""",
            """\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <artifactId>aggregator-api</artifactId>
              <dependencies>
                <dependency>
                  <groupId>org.openmrs.api</groupId>
                  <artifactId>openmrs-api</artifactId>
                  <version>2.7.4</version>
                </dependency>
              </dependencies>
            </project>""",
        )
        self.assertEqual(main, "8")
        self.assertEqual(versions, [8, 11, 17, 21])

    def test_no_openmrs_dep_falls_back_to_compiler(self):
        """Without OpenMRS dep, compiler target is used as fallback."""
        main, versions = self._run_inference("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <maven.compiler.target>17</maven.compiler.target>
              </properties>
              <dependencies>
                <dependency>
                  <groupId>junit</groupId>
                  <artifactId>junit</artifactId>
                  <version>4.13</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertEqual(main, "17")
        self.assertIsNone(versions)

    def test_no_openmrs_dep_no_compiler(self):
        main, versions = self._run_inference("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <dependencies>
                <dependency>
                  <groupId>junit</groupId>
                  <artifactId>junit</artifactId>
                  <version>4.13</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertIsNone(main)
        self.assertIsNone(versions)


if __name__ == "__main__":
    unittest.main()
