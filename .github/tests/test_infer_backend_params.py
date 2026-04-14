#!/usr/bin/env python3
"""Tests for infer_backend_params.py."""

import os
import sys
import tempfile
import textwrap
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import infer_backend_params as infer
from utils import strip_ns


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
# Property resolution
# ---------------------------------------------------------------------------

class TestResolve(unittest.TestCase):
    def test_simple_property(self):
        self.assertEqual(infer.resolve("${foo}", {"foo": "2.6.1"}), "2.6.1")

    def test_chained_property(self):
        props = {"foo": "${bar}", "bar": "2.6.1"}
        self.assertEqual(infer.resolve("${foo}", props), "2.6.1")

    def test_unresolvable(self):
        self.assertEqual(infer.resolve("${missing}", {}), "${missing}")

    def test_literal(self):
        self.assertEqual(infer.resolve("2.6.1", {}), "2.6.1")

    def test_none(self):
        self.assertIsNone(infer.resolve(None, {}))

    def test_max_depth(self):
        props = {"a": "${a}"}
        result = infer.resolve("${a}", props)
        self.assertEqual(result, "${a}")


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
        self.assertEqual(infer.map_to_java("2.7.0"), [8, 11, 17])

    def test_2_7_4(self):
        self.assertEqual(infer.map_to_java("2.7.4"), [8, 11, 17])

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
        self.assertEqual(infer.map_range_to_java("[2.6.0, 2.8.0)"), [8, 11, 17])

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
    def _parse(self, xml_str):
        root = ET.fromstring(textwrap.dedent(xml_str))
        strip_ns(root)
        props = infer.get_properties(root)
        return root, props

    def test_compiler_release_property(self):
        root, props = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <maven.compiler.release>11</maven.compiler.release>
                <maven.compiler.target>8</maven.compiler.target>
              </properties>
            </project>""")
        self.assertEqual(infer.find_compiler_version(root, props), "11")

    def test_compiler_target_property(self):
        root, props = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <maven.compiler.target>1.8</maven.compiler.target>
              </properties>
            </project>""")
        self.assertEqual(infer.find_compiler_version(root, props), "8")

    def test_plugin_config_target(self):
        root, props = self._parse("""\
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
        self.assertEqual(infer.find_compiler_version(root, props), "8")

    def test_plugin_config_release(self):
        root, props = self._parse("""\
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
        self.assertEqual(infer.find_compiler_version(root, props), "17")

    def test_plugin_config_with_property_ref(self):
        root, props = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <javaVersion>21</javaVersion>
              </properties>
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
        self.assertEqual(infer.find_compiler_version(root, props), "21")

    def test_no_compiler_settings(self):
        root, props = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <someOtherProp>value</someOtherProp>
              </properties>
            </project>""")
        self.assertIsNone(infer.find_compiler_version(root, props))

    def test_no_namespace(self):
        root, props = self._parse("""\
            <project>
              <properties>
                <maven.compiler.target>11</maven.compiler.target>
              </properties>
            </project>""")
        self.assertEqual(infer.find_compiler_version(root, props), "11")


# ---------------------------------------------------------------------------
# OpenMRS version detection
# ---------------------------------------------------------------------------

class TestFindOpenmrsVersion(unittest.TestCase):
    def _run(self, root_xml, submodules=None):
        """Parse root XML and optional submodule POMs, return inferred version."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root_path = os.path.join(tmpdir, "pom.xml")
            with open(root_path, "w") as f:
                f.write(textwrap.dedent(root_xml))
            if submodules:
                for name, xml_content in submodules.items():
                    mod_dir = os.path.join(tmpdir, name)
                    os.makedirs(mod_dir, exist_ok=True)
                    with open(os.path.join(mod_dir, "pom.xml"), "w") as f:
                        f.write(textwrap.dedent(xml_content))

            root = ET.parse(root_path).getroot()
            strip_ns(root)
            props = infer.get_properties(root)

            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                return infer.find_openmrs_version(root, props)
            finally:
                os.chdir(old_cwd)

    def test_dependency_management(self):
        result = self._run("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <openmrsVersion>2.6.1</openmrsVersion>
              </properties>
              <dependencyManagement>
                <dependencies>
                  <dependency>
                    <groupId>org.openmrs.api</groupId>
                    <artifactId>openmrs-api</artifactId>
                    <version>${openmrsVersion}</version>
                  </dependency>
                </dependencies>
              </dependencyManagement>
            </project>""")
        self.assertEqual(result, "2.6.1")

    def test_direct_dependency(self):
        result = self._run("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <dependencies>
                <dependency>
                  <groupId>org.openmrs.api</groupId>
                  <artifactId>openmrs-api</artifactId>
                  <version>2.8.0</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertEqual(result, "2.8.0")

    def test_submodule_dependency(self):
        result = self._run(
            """\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <openmrsPlatformVersion>2.7.4</openmrsPlatformVersion>
              </properties>
              <modules>
                <module>api</module>
              </modules>
            </project>""",
            submodules={
                "api": """\
                    <project xmlns="http://maven.apache.org/POM/4.0.0">
                      <dependencies>
                        <dependency>
                          <groupId>org.openmrs.api</groupId>
                          <artifactId>openmrs-api</artifactId>
                          <version>${openmrsPlatformVersion}</version>
                        </dependency>
                      </dependencies>
                    </project>"""
            },
        )
        self.assertEqual(result, "2.7.4")

    def test_openmrs_web(self):
        result = self._run("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <dependencies>
                <dependency>
                  <groupId>org.openmrs.web</groupId>
                  <artifactId>openmrs-web</artifactId>
                  <version>2.4.0</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertEqual(result, "2.4.0")

    def test_no_openmrs_dep(self):
        result = self._run("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <dependencies>
                <dependency>
                  <groupId>junit</groupId>
                  <artifactId>junit</artifactId>
                  <version>4.13</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertIsNone(result)

    def test_version_range(self):
        result = self._run("""\
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
        self.assertEqual(result, "[2.4.0, 2.7.0)")


# ---------------------------------------------------------------------------
# Maven server IDs
# ---------------------------------------------------------------------------

class TestFindServerIds(unittest.TestCase):
    def _parse(self, xml_str):
        root = ET.fromstring(textwrap.dedent(xml_str))
        strip_ns(root)
        return root

    def test_both_ids(self):
        root = self._parse("""\
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
        release_id, snapshot_id = infer.find_server_ids(root)
        self.assertEqual(release_id, "openmrs-repo-modules")
        self.assertEqual(snapshot_id, "openmrs-repo-snapshots")

    def test_only_release(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <distributionManagement>
                <repository>
                  <id>my-releases</id>
                </repository>
              </distributionManagement>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(root)
        self.assertEqual(release_id, "my-releases")
        self.assertIsNone(snapshot_id)

    def test_only_snapshot(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <distributionManagement>
                <snapshotRepository>
                  <id>my-snapshots</id>
                </snapshotRepository>
              </distributionManagement>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(root)
        self.assertIsNone(release_id)
        self.assertEqual(snapshot_id, "my-snapshots")

    def test_no_distribution_management(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <groupId>org.example</groupId>
            </project>""")
        release_id, snapshot_id = infer.find_server_ids(root)
        self.assertIsNone(release_id)
        self.assertIsNone(snapshot_id)

    def test_no_namespace(self):
        root = self._parse("""\
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
        release_id, snapshot_id = infer.find_server_ids(root)
        self.assertEqual(release_id, "releases")
        self.assertEqual(snapshot_id, "snapshots")

    def test_empty_ids(self):
        root = self._parse("""\
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
        release_id, snapshot_id = infer.find_server_ids(root)
        self.assertIsNone(release_id)
        self.assertIsNone(snapshot_id)


# ---------------------------------------------------------------------------
# POM project version
# ---------------------------------------------------------------------------

class TestFindProjectVersion(unittest.TestCase):
    def _parse(self, xml_str):
        root = ET.fromstring(textwrap.dedent(xml_str))
        strip_ns(root)
        return root

    def test_direct_version(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <version>1.2.3</version>
            </project>""")
        self.assertEqual(infer.find_project_version(root), "1.2.3")

    def test_snapshot_version(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <version>2.0.0-SNAPSHOT</version>
            </project>""")
        self.assertEqual(infer.find_project_version(root), "2.0.0-SNAPSHOT")

    def test_parent_version_fallback(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <parent>
                <version>3.1.0</version>
              </parent>
            </project>""")
        self.assertEqual(infer.find_project_version(root), "3.1.0")

    def test_direct_version_takes_precedence(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <version>1.0.0</version>
              <parent>
                <version>2.0.0</version>
              </parent>
            </project>""")
        self.assertEqual(infer.find_project_version(root), "1.0.0")

    def test_no_version(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <groupId>org.example</groupId>
            </project>""")
        self.assertIsNone(infer.find_project_version(root))

    def test_whitespace_stripped(self):
        root = self._parse("""\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <version>  1.0.0  </version>
            </project>""")
        self.assertEqual(infer.find_project_version(root), "1.0.0")

    def test_no_namespace(self):
        root = self._parse("""\
            <project>
              <version>4.5.6</version>
            </project>""")
        self.assertEqual(infer.find_project_version(root), "4.5.6")


# ---------------------------------------------------------------------------
# End-to-end integration tests
# ---------------------------------------------------------------------------

class TestEndToEnd(unittest.TestCase):
    """Integration tests that run the full inference pipeline."""

    def _run_inference(self, root_xml, submodules=None):
        with tempfile.TemporaryDirectory() as tmpdir:
            root_path = os.path.join(tmpdir, "pom.xml")
            with open(root_path, "w") as f:
                f.write(textwrap.dedent(root_xml))
            if submodules:
                for name, xml_content in submodules.items():
                    mod_dir = os.path.join(tmpdir, name)
                    os.makedirs(mod_dir, exist_ok=True)
                    with open(os.path.join(mod_dir, "pom.xml"), "w") as f:
                        f.write(textwrap.dedent(xml_content))

            root = ET.parse(root_path).getroot()
            strip_ns(root)
            props = infer.get_properties(root)
            compiler_version = infer.find_compiler_version(root, props)

            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                openmrs_ver = infer.find_openmrs_version(root, props)
            finally:
                os.chdir(old_cwd)

            java_versions = infer.map_to_java(openmrs_ver) if openmrs_ver else None

            if (
                compiler_version
                and java_versions
                and int(compiler_version) not in java_versions
            ):
                java_versions.append(int(compiler_version))
                java_versions.sort()

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
                    <version>${openmrsPlatformVersion}</version>
                  </dependency>
                </dependencies>
              </dependencyManagement>
            </project>""")
        self.assertEqual(main, "8")
        self.assertEqual(versions, [8, 11])

    def test_openmrs_3_x_target_21(self):
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
        self.assertEqual(main, "21")
        self.assertEqual(versions, [21, 25])

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

    def test_main_is_min_not_compiler_target(self):
        """main_java_version is min(java_versions), not the compiler target."""
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
        self.assertEqual(main, "8")
        self.assertEqual(versions, [8, 11, 17])

    def test_main_java_added_to_versions_if_missing(self):
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
                  <version>3.0.0</version>
                </dependency>
              </dependencies>
            </project>""")
        self.assertEqual(main, "21")
        self.assertIn(21, versions)
        self.assertIn(25, versions)

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
        self.assertEqual(versions, [8, 11, 17])

    def test_submodule_with_parent_properties(self):
        main, versions = self._run_inference(
            """\
            <project xmlns="http://maven.apache.org/POM/4.0.0">
              <properties>
                <maven.compiler.target>8</maven.compiler.target>
                <openmrsPlatformVersion>2.7.4</openmrsPlatformVersion>
              </properties>
              <modules>
                <module>api</module>
              </modules>
            </project>""",
            submodules={
                "api": """\
                    <project xmlns="http://maven.apache.org/POM/4.0.0">
                      <dependencies>
                        <dependency>
                          <groupId>org.openmrs.api</groupId>
                          <artifactId>openmrs-api</artifactId>
                          <version>${openmrsPlatformVersion}</version>
                        </dependency>
                      </dependencies>
                    </project>"""
            },
        )
        self.assertEqual(main, "8")
        self.assertEqual(versions, [8, 11, 17])

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
